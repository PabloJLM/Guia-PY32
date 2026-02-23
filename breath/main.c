#include "RTE_Components.h"

#ifndef PY32F002Ax5
#define PY32F002Ax5
#endif
#include "py32f0xx.h"




const uint8_t wake[50] = {
     0,  2,  4,  6,  8, 10, 12, 14, 16, 18,
    20, 22, 24, 26, 28, 30, 32, 34, 36, 38,
    40, 42, 44, 46, 48, 51, 53, 55, 57, 59,
    61, 63, 65, 67, 69, 71, 73, 75, 77, 79,
    81, 83, 85, 87, 89, 91, 93, 95, 97, 100
};

const uint8_t resp[50] = {
     0,  1,  3,  6, 10, 15, 21, 28, 36, 45,
    55, 65, 75, 84, 90, 94, 97, 99, 100, 99,
    97, 94, 90, 84, 75,

    75, 84, 90, 94, 97, 99, 100, 99, 97, 94,
    90, 84, 75, 65, 55, 45, 36, 28, 21, 15,
    10,  6,  3,  1,  0
};




//funcion delay normal
void delay_ms(uint32_t ms)
{
    for(uint32_t i = 0; i < ms * 2000; i++)
    {
        __NOP();
    }
}

void led_pwm(uint32_t pin, uint8_t brillo)// funcion recibe pin y brillo 
{
    // Genera 100 pulsos con PWM 
    for(uint8_t i = 0; i < 100; i++)
    {
        if(i < brillo)
            GPIOA->ODR |= (1 << pin);   // LED ON
        else
            GPIOA->ODR &= ~(1 << pin);  // LED OFF
        
        // Delay para el pwm, poner mas para mas separacion y lentitud jsjs
        for(volatile uint8_t j = 0; j < 15; j++) __NOP();
    }
}

int main(void)
{
    // 1. Habilitar reloj del GPIOA
    RCC->IOPENR |= RCC_IOPENR_GPIOAEN;
    
    // 2. Configurar PA1, PA5, PA6, PA7 como salidas
    GPIOA->MODER &= ~((3 << (1*2)) | (3 << (5*2)) | (3 << (6*2)) | (3 << (7*2)));
    GPIOA->MODER |=  ((1 << (1*2)) | (1 << (5*2)) | (1 << (6*2)) | (1 << (7*2)));
    
    // 3. Configurar como push-pull, velocidad baja
    GPIOA->OTYPER &= ~((1<<1) | (1<<5) | (1<<6) | (1<<7));
    GPIOA->OSPEEDR &= ~((3 << (1*2)) | (3 << (5*2)) | (3 << (6*2)) | (3 << (7*2)));
    GPIOA->PUPDR &= ~((3 << (1*2)) | (3 << (5*2)) | (3 << (6*2)) | (3 << (7*2)));
    
    while (1)
    {
			
				for(uint8_t i = 0; i <50; i++){
					uint8_t brillo1 = wake[i];
					led_pwm(5, brillo1);
				}
				
				delay_ms(25);
				
				for(uint8_t i = 0; i <50; i++){
					uint8_t brillo1 = wake[i];
					led_pwm(7, brillo1);
				}
				
				delay_ms(25);
				
				for(uint8_t i = 0; i <50; i++){
					uint8_t brillo1 = wake[i];
					led_pwm(6, brillo1);
				}
				
				delay_ms(25);
				
				for(uint8_t i = 0; i <50; i++){
					uint8_t brillo1 = wake[i];
					led_pwm(1, brillo1);
				}
				
				delay_ms(25);
				
				led_pwm(1, 100);  // PA1
        led_pwm(5, 100);  // PA5 
        led_pwm(6, 100);  // PA6
        led_pwm(7, 100);  // PA7
				
				delay_ms(100);
				led_pwm(1, 0); 
				delay_ms(50);
				led_pwm(6, 0);
				delay_ms(50);
				led_pwm(7, 0);
				delay_ms(50);
				led_pwm(5, 0);
				
				
				delay_ms(200);
				
				for(uint8_t i = 0; i < 50; i++)
        {
            uint8_t brillo = resp[i];//recorro el array de respiraciones con el brillo asignado
            
            // Todos los pwm tienen el mismo brillo
            led_pwm(1, brillo);  // PA1
            led_pwm(5, brillo);  // PA5 
            led_pwm(6, brillo);  // PA6
            led_pwm(7, brillo);  // PA7
            
            // Delay entre pasos (ajusta para cambiar velocidad)
            // no bajar de 10ms para que se vea, aumentar segun velocidad 
            delay_ms(1);
        }
				
    }
}
