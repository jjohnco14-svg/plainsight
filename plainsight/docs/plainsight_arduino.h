/* Plainsight's stand-in for Arduino.h -- NOT real hardware.
 * Tests set the inputs and read the outputs through these variables:
 *   plainsight_pin[N]   what digitalRead/analogRead(N) return; what digitalWrite/analogWrite(N, v) wrote
 *   plainsight_millis   what millis() returns (delay(ms) moves it forward)
 * In plans:  pin[2] = HIGH,  millis = 5000,  pin[13] becomes HIGH
 */
#pragma once
#include <stdint.h>
#include <string.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

typedef uint8_t byte;
typedef bool boolean;
#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define LED_BUILTIN 13
#define A0 14
#define A1 15
#define A2 16
#define A3 17
#define A4 18
#define A5 19

static int plainsight_pin[64];
static int plainsight_pin_mode[64];
static unsigned long plainsight_millis;

static inline void pinMode(int p, int m) { plainsight_pin_mode[p & 63] = m; }
static inline void digitalWrite(int p, int v) { plainsight_pin[p & 63] = v ? 1 : 0; }
static inline int digitalRead(int p) { return plainsight_pin[p & 63] ? 1 : 0; }
static inline int analogRead(int p) { return plainsight_pin[p & 63]; }
static inline void analogWrite(int p, int v) { plainsight_pin[p & 63] = v; }
static inline unsigned long millis(void) { return plainsight_millis; }
static inline unsigned long micros(void) { return plainsight_millis * 1000UL; }
static inline void delay(unsigned long ms) { plainsight_millis += ms; }
static inline long map(long x, long a, long b, long c, long d) { return (x - a) * (d - c) / (b - a) + c; }
#define constrain(x, lo, hi) ((x) < (lo) ? (lo) : ((x) > (hi) ? (hi) : (x)))
#ifndef min
#define min(a, b) ((a) < (b) ? (a) : (b))
#endif
#ifndef max
#define max(a, b) ((a) > (b) ? (a) : (b))
#endif

struct PlainsightSerial {
    void begin(long) {}
    void print(const char *s) { printf("%s", s); }
    void print(char c) { printf("%c", c); }
    void print(int v) { printf("%d", v); }
    void print(unsigned int v) { printf("%u", v); }
    void print(long v) { printf("%ld", v); }
    void print(unsigned long v) { printf("%lu", v); }
    void print(double v) { printf("%.2f", v); }
    void println() { printf("\n"); }
    template <typename T> void println(T v) { print(v); printf("\n"); }
};
static PlainsightSerial Serial;
