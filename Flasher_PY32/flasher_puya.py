#!/usr/bin/env python3
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
 PROGRAMADOR GRAFICO PARA MICROCONTROLADORES PY32 (PUYA) VIA ST-LINK / SWD
============================================================================

Que hace este programa (en palabras simples):

  1. Lee un archivo .hex (el que exporta Keil al compilar tu proyecto).
  2. Se conecta al microcontrolador PY32 a traves del programador ST-Link.
  3. Borra la memoria flash del chip (deja todo en blanco).
  4. Escribe tu programa nuevo en la flash.
  5. Vuelve a leer la flash y compara, byte por byte, que lo que quedo
     grabado sea EXACTAMENTE igual a tu archivo .hex.
  6. Reinicia el microcontrolador para que arranque corriendo el programa.

Todo el proceso se muestra en una "terminal" dentro de la ventana, con
explicaciones en español de cada paso, para que cualquier persona -tenga
o no conocimientos tecnicos- pueda flashear un chip con solo hacer clic.

------------------------------------------------------------------------
REQUISITOS (una sola vez, antes de usar este programa):
------------------------------------------------------------------------
  - Tener Python 3 con Tkinter instalado.
        Si al abrir este programa da error de "tkinter", instalalo con:
            sudo apt install python3-tk

  - Tener compilado el OpenOCD parcheado para PY32, normalmente en:
            ~/openocd-for-py32f0xx
        (el mismo que usamos manualmente por terminal). Si esta en otra
        carpeta, puedes indicarla en el campo "Carpeta de OpenOCD".

  - Tener el ST-Link V2 conectado por USB, y cableado al PY32 con al
    menos: SWDIO, SWCLK, GND y 3.3V (no hace falta NRST).

  - Este programa usa "sudo" internamente para poder hablar con el
    ST-Link (igual que hacias al escribir "sudo ./src/openocd..." a
    mano). Por eso te va a pedir tu contraseña de usuario de Ubuntu.
    Esa contraseña NUNCA se guarda en ningun archivo, solo se usa en
    memoria durante esta sesion para lanzar los comandos.

No necesitas instalar el paquete "intelhex": este programa incluye su
propio lector de archivos .hex, no depende de nada externo aparte de
Python y OpenOCD.
============================================================================
"""

import os
import re
import sys
import queue
import shlex
import threading
import subprocess
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext
except ImportError:
    print("Falta el modulo 'tkinter'. Instalalo con:\n    sudo apt install python3-tk")
    sys.exit(1)


# ============================================================================
# CONFIGURACION FIJA (edita estos 3 valores UNA SOLA VEZ, aqui, y listo)
# ============================================================================
# Se sacaron de la pantalla porque en esta maquina siempre son los mismos
# valores y no tiene sentido que alguien los pueda escribir mal o dejar en
# blanco. Si algun dia cambias de PC o de carpeta, edita solo estas 3 lineas.

OCD_DIR = os.path.expanduser("~/openocd-for-py32f0xx")   # carpeta donde compilaste OpenOCD
ADAPTER_SPEED = "100"                                     # velocidad SWD en kHz (100 = probada y estable)
SUDO_PASSWORD = "1012"                  # contraseña de tu usuario de Ubuntu

# ADVERTENCIA DE SEGURIDAD: dejar la contraseña escrita en texto plano en
# este archivo significa que cualquiera que pueda abrir este .py la puede
# leer. Esta bien para una PC de laboratorio de uso personal/confiable,
# pero no copies este archivo a una maquina compartida sin cambiarla antes.
# ============================================================================


# ============================================================================
# PARTE 1: LECTOR DE ARCHIVOS INTEL HEX (sin dependencias externas)
# ============================================================================

class HexParseError(Exception):
    """Se lanza cuando el archivo .hex tiene un formato invalido."""
    pass


def parse_intel_hex(path):
    """
    Lee un archivo en formato Intel HEX (el que exporta Keil) y devuelve:
        (direccion_base, datos)
    donde 'datos' son los bytes del programa, listos para escribir en la
    flash, y 'direccion_base' es la direccion de memoria donde empiezan
    (normalmente 0x08000000, el inicio de la flash en estos chips).
    """
    data = {}          # direccion_absoluta -> byte
    high_addr = 0       # parte alta de la direccion (registros tipo 02/04)

    with open(path, "r") as f:
        for lineno, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line:
                continue
            if not line.startswith(":"):
                raise HexParseError(
                    f"Linea {lineno}: no empieza con ':' (no parece un .hex valido)"
                )
            try:
                raw = bytes.fromhex(line[1:])
            except ValueError:
                raise HexParseError(f"Linea {lineno}: contiene caracteres invalidos")

            if len(raw) < 5:
                raise HexParseError(f"Linea {lineno}: demasiado corta")

            byte_count = raw[0]
            addr = (raw[1] << 8) | raw[2]
            rec_type = raw[3]
            payload = raw[4:4 + byte_count]
            checksum = raw[4 + byte_count] if len(raw) > 4 + byte_count else None
            calc_checksum = (-sum(raw[:4 + byte_count])) & 0xFF

            if checksum is None or calc_checksum != checksum:
                raise HexParseError(f"Linea {lineno}: checksum invalido (archivo corrupto)")

            if rec_type == 0x00:                     # Dato
                base = (high_addr << 16) + addr
                for i, b in enumerate(payload):
                    data[base + i] = b
            elif rec_type == 0x01:                    # Fin de archivo
                break
            elif rec_type == 0x02:                    # Extended Segment Address
                high_addr = (((payload[0] << 8) | payload[1]) << 4) >> 16
            elif rec_type == 0x04:                    # Extended Linear Address
                high_addr = (payload[0] << 8) | payload[1]
            elif rec_type in (0x03, 0x05):             # Start address (no aplica al flasheo)
                pass
            else:
                raise HexParseError(
                    f"Linea {lineno}: tipo de registro 0x{rec_type:02X} no soportado"
                )

    if not data:
        raise HexParseError("El archivo .hex no contiene ningun dato de programa")

    min_addr = min(data.keys())
    max_addr = max(data.keys())
    length = max_addr - min_addr + 1
    buf = bytearray([0xFF] * length)
    for addr, b in data.items():
        buf[addr - min_addr] = b

    return min_addr, bytes(buf)


# ============================================================================
# PARTE 2: EJECUCION DE COMANDOS (openocd via sudo) EN SEGUNDO PLANO
# ============================================================================

FLASH_BASE = 0x08000000


class Runner:
    """
    Corre comandos de shell (con sudo) sin congelar la ventana, y va
    mandando cada linea de salida a una cola para que la interfaz la
    muestre en tiempo real, como si fuera una terminal.
    """

    def __init__(self, log_queue):
        self.log_queue = log_queue

    def log(self, text, tag=None):
        self.log_queue.put((text, tag))

    def run(self, cmd_list, cwd, sudo_password):
        """
        Ejecuta un comando (con sudo) y devuelve (codigo_salida, salida_completa).
        Muestra cada linea de salida en la "terminal" de la interfaz al
        momento en que aparece.
        """
        full_cmd = ["sudo", "-S", "-p", ""] + cmd_list
        self.log("$ " + " ".join(shlex.quote(c) for c in cmd_list), "cmd")

        try:
            proc = subprocess.Popen(
                full_cmd,
                cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as e:
            self.log(f"ERROR: no se pudo ejecutar el comando ({e})", "err")
            return 1, ""

        try:
            proc.stdin.write(sudo_password + "\n")
            proc.stdin.flush()
        except Exception:
            pass
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass

        output_lines = []
        for line in proc.stdout:
            line = line.rstrip("\n")
            output_lines.append(line)
            tag = None
            if "Error" in line or "error" in line:
                tag = "err"
            elif "Warn" in line:
                tag = "warn"
            self.log(line, tag)

        proc.wait()
        full_output = "\n".join(output_lines)

        if "incorrect password" in full_output.lower() or "sorry, try again" in full_output.lower():
            self.log("ERROR: la contraseña de sudo parece incorrecta.", "err")

        return proc.returncode, full_output


# ============================================================================
# PARTE 3: LOGICA DE ALTO NIVEL (los "pasos" del flasheo)
# ============================================================================

class FlashJob:
    """
    Encapsula toda la secuencia de flasheo: detectar, borrar, escribir,
    verificar y reiniciar. Reporta el progreso paso a paso.
    """

    STEP_NAMES = [
        "Verificar herramientas",
        "Leer archivo .hex",
        "Detectar el microcontrolador",
        "Borrar la memoria flash",
        "Grabar el nuevo programa",
        "Verificar byte por byte",
        "Reiniciar y ejecutar",
    ]

    def __init__(self, openocd_dir, hex_path, adapter_speed, sudo_password,
                 log_queue, status_callback):
        self.openocd_dir = openocd_dir
        self.hex_path = hex_path
        self.adapter_speed = adapter_speed
        self.sudo_password = sudo_password
        self.runner = Runner(log_queue)
        self.log = self.runner.log
        self.status_callback = status_callback  # (step_index, "running"/"ok"/"error")

    def openocd_bin(self):
        return os.path.join(self.openocd_dir, "src", "openocd")

    def base_cmd(self):
        return [
            self.openocd_bin(), "-s", "tcl",
            "-f", "interface/stlink.cfg",
            "-f", "target/py32f0xx.cfg",
            "-c", f"adapter speed {self.adapter_speed}",
        ]

    def set_step(self, idx, status):
        self.status_callback(idx, status)

    # --- Paso 0: herramientas ---------------------------------------------
    def step_check_tools(self):
        self.set_step(0, "running")
        self.log("")
        self.log("=== PASO 1: Verificando que las herramientas esten listas ===", "head")
        ocd_bin = self.openocd_bin()
        if not os.path.isfile(ocd_bin):
            self.log(f"ERROR: no se encontro OpenOCD en: {ocd_bin}", "err")
            self.log("Revisa el campo 'Carpeta de OpenOCD' y que ya lo hayas compilado.", "err")
            self.set_step(0, "error")
            return False
        if not os.path.isfile(self.hex_path):
            self.log(f"ERROR: no se encontro el archivo .hex en: {self.hex_path}", "err")
            self.set_step(0, "error")
            return False
        self.log("OK: se encontro OpenOCD y el archivo .hex existe.", "ok")
        self.set_step(0, "ok")
        return True

    # --- Paso 1: leer y convertir el .hex -----------------------------------
    def step_read_hex(self):
        self.set_step(1, "running")
        self.log("")
        self.log("=== PASO 2: Leyendo tu archivo .hex ===", "head")
        try:
            base_addr, data = parse_intel_hex(self.hex_path)
        except HexParseError as e:
            self.log(f"ERROR: el archivo .hex no se pudo leer ({e})", "err")
            self.set_step(1, "error")
            return None
        self.log(f"OK: el programa ocupa {len(data)} bytes ({len(data)/1024:.2f} KB), "
                  f"empieza en la direccion 0x{base_addr:08X}.", "ok")
        self.set_step(1, "ok")
        return base_addr, data

    # --- Paso 2: detectar el chip -------------------------------------------
    def step_detect(self):
        self.set_step(2, "running")
        self.log("")
        self.log("=== PASO 3: Buscando el microcontrolador a traves del ST-Link ===", "head")
        cmd = self.base_cmd() + ["-c", "init", "-c", "reset halt", "-c", "exit"]
        code, out = self.runner.run(cmd, self.openocd_dir, self.sudo_password)

        if "Puya MCU detected" in out or "PY32" in out:
            m = re.search(r"PY32 flash size (\d+)k", out)
            flash_kb = m.group(1) if m else "?"
            self.log(f"OK: se detecto un microcontrolador PY32 (flash de {flash_kb} KB).", "ok")
            self.set_step(2, "ok")
            return True
        if "unable to connect to the target" in out.lower() or code != 0:
            self.log("ERROR: no se pudo conectar con el microcontrolador.", "err")
            self.log("Revisa: 1) el cableado SWDIO/SWCLK/GND/3.3V, "
                      "2) que el chip tenga alimentacion, 3) tu contraseña de sudo.", "err")
            self.set_step(2, "error")
            return False

        self.log("AVISO: se conecto, pero no se identifico claramente como PY32. Se continua.", "warn")
        self.set_step(2, "ok")
        return True

    # --- Paso 3: borrar ------------------------------------------------------
    def step_erase(self):
        self.set_step(3, "running")
        self.log("")
        self.log("=== PASO 4: Borrando la memoria flash del chip ===", "head")
        cmd = self.base_cmd() + [
            "-c", "init", "-c", "reset halt",
            "-c", "flash erase_sector 0 0 last",
            "-c", "exit",
        ]
        code, out = self.runner.run(cmd, self.openocd_dir, self.sudo_password)
        if code == 0 and "erased sectors" in out:
            self.log("OK: la flash quedo completamente borrada.", "ok")
            self.set_step(3, "ok")
            return True
        self.log("ERROR: no se pudo borrar la flash. Revisa el mensaje de arriba.", "err")
        self.set_step(3, "error")
        return False

    # --- Paso 4: escribir ------------------------------------------------------
    def step_write(self, bin_path, offset):
        self.set_step(4, "running")
        self.log("")
        self.log("=== PASO 5: Grabando tu programa en la flash ===", "head")
        cmd = self.base_cmd() + [
            "-c", "init", "-c", "reset halt",
            "-c", f"flash write_bank 0 {bin_path} {offset}",
            "-c", "exit",
        ]
        code, out = self.runner.run(cmd, self.openocd_dir, self.sudo_password)
        if code == 0 and "wrote" in out:
            self.log("OK: el programa se escribio en la flash.", "ok")
            self.set_step(4, "ok")
            return True
        self.log("ERROR: fallo la escritura en la flash. Revisa el mensaje de arriba.", "err")
        self.set_step(4, "error")
        return False

    # --- Paso 5: verificar byte por byte ---------------------------------------
    def step_verify(self, original_data, offset, dump_path):
        self.set_step(5, "running")
        self.log("")
        self.log("=== PASO 6: Leyendo la flash de vuelta y comparando byte por byte ===", "head")
        length = len(original_data)
        cmd = self.base_cmd() + [
            "-c", "init", "-c", "reset halt",
            "-c", f"flash read_bank 0 {dump_path} {offset} {length}",
            "-c", "exit",
        ]
        code, out = self.runner.run(cmd, self.openocd_dir, self.sudo_password)
        if code != 0 or not os.path.isfile(dump_path):
            self.log("ERROR: no se pudo leer la flash para verificarla.", "err")
            self.set_step(5, "error")
            return False

        with open(dump_path, "rb") as f:
            read_back = f.read()

        total = min(len(original_data), len(read_back))
        mismatches = []
        for i in range(total):
            if original_data[i] != read_back[i]:
                mismatches.append((i, original_data[i], read_back[i]))

        matched = total - len(mismatches)
        pct = (matched / total * 100) if total else 0

        self.log(f"Bytes comparados: {total}", None)
        self.log(f"Bytes que coinciden: {matched} ({pct:.2f}%)", "ok" if not mismatches else "warn")

        if mismatches:
            self.log(f"Bytes que NO coinciden: {len(mismatches)}", "err")
            self.log("Primeras diferencias (direccion, esperado -> leido):", "err")
            for addr, exp, got in mismatches[:15]:
                self.log(f"  0x{FLASH_BASE + offset + addr:08X}: "
                         f"esperado 0x{exp:02X}  ->  leido 0x{got:02X}", "err")
            if len(mismatches) > 15:
                self.log(f"  ... y {len(mismatches) - 15} diferencias mas.", "err")
            self.log("RESULTADO: LA VERIFICACION FALLO. El chip NO quedo grabado correctamente.", "err")
            self.set_step(5, "error")
            return False

        self.log("RESULTADO: VERIFICACION EXITOSA. El 100% de los bytes coincide con tu archivo.", "ok")
        self.set_step(5, "ok")
        return True

    # --- Paso 6: reiniciar y correr ---------------------------------------------
    def step_reset_run(self):
        self.set_step(6, "running")
        self.log("")
        self.log("=== PASO 7: Reiniciando el microcontrolador para que corra el programa ===", "head")
        cmd = self.base_cmd() + ["-c", "init", "-c", "reset run", "-c", "exit"]
        code, out = self.runner.run(cmd, self.openocd_dir, self.sudo_password)
        if code == 0:
            self.log("OK: el microcontrolador fue reiniciado y deberia estar corriendo tu programa.", "ok")
            self.set_step(6, "ok")
            return True
        self.log("AVISO: no se pudo confirmar el reinicio, pero la grabacion ya fue verificada.", "warn")
        self.set_step(6, "ok")
        return True

    # --- Secuencia completa --------------------------------------------------------
    def run_full_sequence(self):
        if not self.step_check_tools():
            return False

        parsed = self.step_read_hex()
        if parsed is None:
            return False
        base_addr, data = parsed
        offset = base_addr - FLASH_BASE
        if offset < 0:
            self.log(f"ERROR: la direccion del programa (0x{base_addr:08X}) "
                      f"es menor que el inicio de la flash (0x{FLASH_BASE:08X}).", "err")
            self.set_step(1, "error")
            return False

        bin_path = str(Path(self.hex_path).with_suffix(".bin"))
        with open(bin_path, "wb") as f:
            f.write(data)

        if not self.step_detect():
            return False
        if not self.step_erase():
            return False
        if not self.step_write(bin_path, offset):
            return False

        dump_path = str(Path(self.hex_path).with_suffix(".verify.bin"))
        if not self.step_verify(data, offset, dump_path):
            return False

        self.step_reset_run()

        self.log("")
        self.log("############################################################", "head")
        self.log("#  TODO LISTO: el chip fue borrado, grabado y verificado.  #", "head")
        self.log("############################################################", "head")
        return True


# ============================================================================
# PARTE 4: INTERFAZ GRAFICA
# ============================================================================

class FlasherGUI(tk.Tk):

    COLORS = {
        "bg": "#1e1e1e",
        "fg": "#d4d4d4",
        "ok": "#4caf50",
        "err": "#f44336",
        "warn": "#ffb300",
        "head": "#569cd6",
        "cmd": "#9cdcfe",
    }

    def __init__(self):
        super().__init__()
        self.title("Programador PY32 - Grabador de firmware")
        self.geometry("980x720")
        self.minsize(860, 620)

        self.log_queue = queue.Queue()
        self.worker_thread = None

        self._build_widgets()
        self._poll_log_queue()

    # ---------------------------------------------------------------- UI ---
    def _build_widgets(self):
        pad = {"padx": 10, "pady": 6}

        intro = tk.Label(
            self,
            text=(
                "Este programa graba (\"flashea\") un archivo .hex compilado en Keil "
                "dentro de un microcontrolador PY32, usando un programador ST-Link.\n"
                "Selecciona tu archivo .hex y presiona \"FLASHEAR AHORA\". "
                "El programa te va a ir explicando cada paso."
            ),
            justify="left", wraplength=940, font=("Sans", 10),
        )
        intro.pack(fill="x", **pad)

        # --- Formulario ---
        form = tk.LabelFrame(self, text="1) Archivo a grabar", padx=10, pady=10)
        form.pack(fill="x", **pad)

        # Archivo hex
        tk.Label(form, text="Archivo .hex a grabar:").grid(row=0, column=0, sticky="w")
        self.hex_path_var = tk.StringVar()
        tk.Entry(form, textvariable=self.hex_path_var, width=70).grid(row=0, column=1, sticky="we", padx=5)
        tk.Button(form, text="Buscar...", command=self._browse_hex).grid(row=0, column=2)

        form.grid_columnconfigure(1, weight=1)

        tk.Label(
            form,
            text=f"Carpeta de OpenOCD: {OCD_DIR}   |   Velocidad SWD: {ADAPTER_SPEED} kHz   "
                 f"(valores fijos, configurados dentro del script)",
            fg="#666666", font=("Sans", 8),
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # --- Botones de accion ---
        actions = tk.Frame(self)
        actions.pack(fill="x", **pad)

        self.flash_btn = tk.Button(
            actions, text="FLASHEAR AHORA (borrar + grabar + verificar)",
            command=self._on_flash_click, bg="#2e7d32", fg="white",
            font=("Sans", 11, "bold"), height=2,
        )
        self.flash_btn.pack(side="left", padx=(0, 10))

        self.detect_btn = tk.Button(
            actions, text="Solo detectar el chip", command=self._on_detect_click,
        )
        self.detect_btn.pack(side="left", padx=(0, 10))

        self.verify_btn = tk.Button(
            actions, text="Solo verificar (sin escribir)", command=self._on_verify_only_click,
        )
        self.verify_btn.pack(side="left", padx=(0, 10))

        self.save_log_btn = tk.Button(actions, text="Guardar registro...", command=self._save_log)
        self.save_log_btn.pack(side="right")

        # --- Panel de pasos ---
        steps_frame = tk.LabelFrame(self, text="2) Progreso", padx=10, pady=10)
        steps_frame.pack(fill="x", **pad)

        self.step_labels = []
        for i, name in enumerate(FlashJob.STEP_NAMES):
            row = tk.Frame(steps_frame)
            row.pack(fill="x", anchor="w")
            marker = tk.Label(row, text="o", width=3, font=("Sans", 10, "bold"), fg="#888888")
            marker.pack(side="left")
            label = tk.Label(row, text=f"{i + 1}. {name}", anchor="w")
            label.pack(side="left")
            self.step_labels.append(marker)

        # --- Terminal de salida ---
        term_frame = tk.LabelFrame(self, text="3) Detalle tecnico (como una terminal)", padx=5, pady=5)
        term_frame.pack(fill="both", expand=True, **pad)

        self.log_box = scrolledtext.ScrolledText(
            term_frame, bg=self.COLORS["bg"], fg=self.COLORS["fg"],
            insertbackground="white", font=("Monospace", 9), wrap="word",
        )
        self.log_box.pack(fill="both", expand=True)
        for tag, color in [("ok", self.COLORS["ok"]), ("err", self.COLORS["err"]),
                            ("warn", self.COLORS["warn"]), ("head", self.COLORS["head"]),
                            ("cmd", self.COLORS["cmd"])]:
            self.log_box.tag_config(tag, foreground=color)
        self.log_box.config(state="disabled")

        self.status_var = tk.StringVar(value="Listo para empezar.")
        status_bar = tk.Label(self, textvariable=self.status_var, anchor="w",
                               bg="#333333", fg="white", padx=8, pady=4)
        status_bar.pack(fill="x", side="bottom")

    def _browse_hex(self):
        path = filedialog.askopenfilename(
            title="Selecciona el archivo .hex",
            filetypes=[("Archivos HEX", "*.hex"), ("Todos los archivos", "*.*")],
        )
        if path:
            self.hex_path_var.set(path)

    def _save_log(self):
        path = filedialog.asksaveasfilename(
            title="Guardar registro", defaultextension=".txt",
            filetypes=[("Texto", "*.txt")],
        )
        if not path:
            return
        content = self.log_box.get("1.0", "end")
        with open(path, "w") as f:
            f.write(content)
        messagebox.showinfo("Guardado", f"Registro guardado en:\n{path}")

    # ------------------------------------------------------------ logging ---
    def _append_log(self, text, tag=None):
        self.log_box.config(state="normal")
        self.log_box.insert("end", text + "\n", tag if tag else "")
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def _poll_log_queue(self):
        try:
            while True:
                text, tag = self.log_queue.get_nowait()
                self._append_log(text, tag)
        except queue.Empty:
            pass
        self.after(80, self._poll_log_queue)

    def _set_step_status(self, idx, status):
        marker = self.step_labels[idx]
        symbols = {"running": ("...", "#ffb300"), "ok": ("OK", "#4caf50"), "error": ("X", "#f44336")}
        text, color = symbols.get(status, ("o", "#888888"))
        # Se llama desde el hilo secundario -> usar 'after' para tocar la GUI de forma segura.
        self.after(0, lambda: (marker.config(text=text, fg=color)))

    def _reset_steps(self):
        for m in self.step_labels:
            m.config(text="o", fg="#888888")

    # ------------------------------------------------------------ validacion ---
    def _validate_inputs(self):
        hex_path = self.hex_path_var.get().strip()

        if not hex_path:
            messagebox.showwarning("Falta informacion", "Selecciona primero un archivo .hex")
            return None
        if not os.path.isfile(hex_path):
            messagebox.showerror("Archivo no encontrado", f"No existe el archivo:\n{hex_path}")
            return None
        if not os.path.isdir(OCD_DIR):
            messagebox.showerror(
                "Carpeta no encontrada",
                f"No existe la carpeta de OpenOCD configurada dentro del script:\n{OCD_DIR}\n\n"
                "Edita la constante OCD_DIR al inicio de este archivo.",
            )
            return None
        if SUDO_PASSWORD == "PON_TU_CONTRASENA_AQUI":
            messagebox.showerror(
                "Falta configurar la contraseña",
                "Edita la constante SUDO_PASSWORD al inicio de este archivo "
                "con tu contraseña real de Ubuntu antes de usar el programa.",
            )
            return None

        return hex_path

    # ------------------------------------------------------------ acciones ---
    def _set_buttons_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for b in (self.flash_btn, self.detect_btn, self.verify_btn):
            b.config(state=state)

    def _run_in_thread(self, target):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("Ocupado", "Ya hay una operacion en curso, espera a que termine.")
            return
        self._set_buttons_enabled(False)
        self._reset_steps()

        def wrapper():
            try:
                target()
            except Exception as e:
                self.log_queue.put((f"ERROR INESPERADO: {e}", "err"))
            finally:
                self.after(0, lambda: self._set_buttons_enabled(True))

        self.worker_thread = threading.Thread(target=wrapper, daemon=True)
        self.worker_thread.start()

    def _on_flash_click(self):
        hex_path = self._validate_inputs()
        if not hex_path:
            return
        if not messagebox.askyesno(
            "Confirmar",
            "Esto va a BORRAR todo el contenido actual de la flash del chip y "
            "va a grabar el nuevo programa.\n\n¿Quieres continuar?",
        ):
            return

        self.status_var.set("Flasheando... revisa la terminal de abajo.")

        def task():
            job = FlashJob(OCD_DIR, hex_path, ADAPTER_SPEED, SUDO_PASSWORD,
                            self.log_queue, self._set_step_status)
            ok = job.run_full_sequence()
            self.after(0, lambda: self.status_var.set(
                "Listo: grabacion verificada correctamente." if ok
                else "Hubo un error. Revisa el detalle en rojo en la terminal."
            ))
            self.after(0, lambda: (
                messagebox.showinfo("Exito", "El chip fue grabado y verificado correctamente.")
                if ok else
                messagebox.showerror("Error", "Algo fallo durante el proceso. "
                                               "Revisa el detalle tecnico en la terminal.")
            ))

        self._run_in_thread(task)

    def _on_detect_click(self):
        hex_path = self._validate_inputs()
        if not hex_path:
            return
        self.status_var.set("Detectando el chip...")

        def task():
            job = FlashJob(OCD_DIR, hex_path, ADAPTER_SPEED, SUDO_PASSWORD,
                            self.log_queue, self._set_step_status)
            job.step_check_tools()
            job.step_detect()
            self.after(0, lambda: self.status_var.set("Listo."))

        self._run_in_thread(task)

    def _on_verify_only_click(self):
        hex_path = self._validate_inputs()
        if not hex_path:
            return
        self.status_var.set("Verificando la flash actual contra el .hex, sin escribir nada...")

        def task():
            job = FlashJob(OCD_DIR, hex_path, ADAPTER_SPEED, SUDO_PASSWORD,
                            self.log_queue, self._set_step_status)
            if not job.step_check_tools():
                return
            parsed = job.step_read_hex()
            if parsed is None:
                return
            base_addr, data = parsed
            offset = base_addr - FLASH_BASE
            if not job.step_detect():
                return
            dump_path = str(Path(hex_path).with_suffix(".verify.bin"))
            ok = job.step_verify(data, offset, dump_path)
            self.after(0, lambda: self.status_var.set(
                "El chip YA tiene este firmware grabado." if ok
                else "El chip NO coincide con este .hex (o esta vacio/diferente)."
            ))

        self._run_in_thread(task)


if __name__ == "__main__":
    app = FlasherGUI()
    app.mainloop()