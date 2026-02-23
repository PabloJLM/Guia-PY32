# PY32F002A – Guía de Programación en Keil uVision  
Implementación con acceso directo a registros (CMSIS)

---

## Descripción

Este repositorio documenta la configuración y programación del microcontrolador **PY32F002A (ARM Cortex-M0+)** en Keil uVision, utilizando CMSIS y acceso directo a registros.

El proyecto implementa:

- Inicialización manual de GPIO
- Generación de PWM por software
- Efectos de encendido
- Control simultáneo de múltiples pines

El código fue desarrollado para el **reto de soldadura SMD**

---

## Plataforma

- Microcontrolador: PY32F002A  
- Núcleo: ARM Cortex-M0+  
- IDE: Keil uVision  
- Framework: CMSIS (Common Microcontroller Software Interface Standard)
- Método: Programacion de registros

Referencia técnica:  
PY32F002A Reference Manual v1.0
PY32F002A Datasheet v0.2

---

## Configuración del Proyecto en Keil

1. Instalar el paquete del dispositivo PUYA.
2. Crear un proyecto nuevo en una carpeta independiente.
3. Seleccionar el dispositivo: `PY32F002Ax5`.
4. Activar:
   - CMSIS Core
   - Device Startup
5. Crear el archivo `main.c` dentro del Source Group.

Si ocurre error de definición del microcontrolador, agregar al inicio del archivo:

```c
#ifndef PY32F002Ax5
#define PY32F002Ax5
#endif

```
## Configuración de Hardware

### Habilitación de reloj GPIO
```c
RCC->IOPENR |= RCC_IOPENR_GPIOAEN;
```
Se habilita el reloj para el puerto A.


## Configuración de Pines

Pines utilizados:
- PA1
- PA5
- PA6
- PA7