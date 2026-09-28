#include <stdio.h>
#include <stdbool.h>

#define STABLE_SAMPLES 3

typedef enum { RELEASED, PRESSED } ButtonState;

typedef struct {
    ButtonState stable;
    bool last_raw;
    int same_count;
    int presses;
} Debouncer;

void db_init(Debouncer *d) {
    d->stable = RELEASED;
    d->last_raw = false;
    d->same_count = 0;
    d->presses = 0;
}

void db_sample(Debouncer *d, bool raw) {
    if (raw == d->last_raw) {
        d->same_count++;
    } else {
        d->same_count = 1;
        d->last_raw = raw;
    }
    if (d->same_count < STABLE_SAMPLES) {
        return;
    }
    switch (d->stable) {
        case RELEASED:
            if (raw) {
                d->stable = PRESSED;
                d->presses++;
            }
            break;
        case PRESSED:
            if (!raw) {
                d->stable = RELEASED;
            }
            break;
    }
}

int main(void) {
    const bool samples[] = {0, 1, 0, 1, 1, 1, 1, 0, 0, 0, 1, 0, 1, 1, 1, 0};
    int n = sizeof(samples) / sizeof(samples[0]);
    Debouncer d;
    db_init(&d);
    for (int i = 0; i < n; i++) {
        db_sample(&d, samples[i]);
    }
    printf("presses: %d\n", d.presses);
    return 0;
}
