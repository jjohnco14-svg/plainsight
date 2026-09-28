#include <stdbool.h>

#define LOW_LEVEL 20     /* percent: start pumping below this */
#define HIGH_LEVEL 90    /* percent: stop pumping at or above this */
#define MAX_FAULTS 3

typedef enum { IDLE, PUMPING, FAULT } PumpState;

typedef struct {
    PumpState state;
    int faults;
    int run_minutes;
} Pump;

void pump_init(Pump *p) {
    p->state = IDLE;
    p->faults = 0;
    p->run_minutes = 0;
}

/* Called once a minute with the tank level (percent) and whether water is flowing. */
void pump_step(Pump *p, int level, bool flowing) {
    switch (p->state) {
        case IDLE:
            if (level < LOW_LEVEL) {
                p->state = PUMPING;
                p->run_minutes = 0;
            }
            break;
        case PUMPING:
            p->run_minutes++;
            if (!flowing && p->run_minutes > 2) {
                p->faults++;
                p->state = p->faults >= MAX_FAULTS ? FAULT : IDLE;
            } else if (level >= HIGH_LEVEL) {
                p->state = IDLE;
            }
            break;
        case FAULT:
            break;
    }
}

int minutes_to_fill(const int *levels, int n) {
    int start = -1;
    for (int i = 0; i < n; i++) {
        if (start < 0 && levels[i] < LOW_LEVEL) {
            start = i;
        }
        if (start >= 0 && levels[i] >= HIGH_LEVEL) {
            return i - start;
        }
    }
    return -1;
}
