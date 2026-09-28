#include <stdbool.h>

#define OPEN_TIME 3
#define JAM_TIME 5

typedef enum { CLOSED, OPEN, JAMMED } GateMode;

typedef struct {
    GateMode mode;
    int open_seconds;
    int blocked_seconds;
    int jams;
} Gate;

/* Called once per second. */
void gate_tick(Gate *g, bool box_arrived, bool bin_full, bool sensor_blocked, bool reset_pressed) {
    switch (g->mode) {
        case CLOSED:
            if (box_arrived && !bin_full) {
                g->mode = OPEN;
                g->open_seconds = 0;
                g->blocked_seconds = 0;
            }
            break;
        case OPEN:
            g->open_seconds++;
            if (sensor_blocked) {
                g->blocked_seconds++;
            } else {
                g->blocked_seconds = 0;
            }
            if (g->blocked_seconds >= JAM_TIME) {
                g->mode = JAMMED;
            } else if (g->open_seconds >= OPEN_TIME && !sensor_blocked) {
                g->mode = CLOSED;
            }
            break;
        case JAMMED:
            if (reset_pressed) {
                g->jams++;
                g->mode = CLOSED;
            }
            break;
    }
}
