#include <stdio.h>
#include <stdbool.h>

#define WINDOW 5
#define TARGET_C 22.0

typedef enum { HEAT_OFF, HEAT_ON } HeatState;

static double readings[WINDOW];
static int next_slot = 0;
static int filled = 0;
bool heater_on = false;

void add_reading(double celsius) {
    readings[next_slot] = celsius;
    next_slot = (next_slot + 1) % WINDOW;
    if (filled < WINDOW) {
        filled++;
    }
}

double average(void) {
    if (filled == 0) {
        return 0.0;
    }
    double sum = 0.0;
    for (int i = 0; i < filled; i++) {
        sum += readings[i];
    }
    return sum / filled;
}

void control(void) {
    double avg = average();
    if (avg < TARGET_C - 0.5 && !heater_on) {
        heater_on = true;
        printf("heater ON at %.1f C\n", avg);
    } else if (avg > TARGET_C + 0.5 && heater_on) {
        heater_on = false;
        printf("heater OFF at %.1f C\n", avg);
    }
}

HeatState state_of(void) {
    return heater_on ? HEAT_ON : HEAT_OFF;
}

void clamp_all(double *values, int n, double lo, double hi) {
    for (int i = 0; i < n; i++) {
        if (values[i] < lo) {
            values[i] = lo;
        } else if (values[i] > hi) {
            values[i] = hi;
        }
    }
}
