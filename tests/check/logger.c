#define SLOTS 4

typedef enum { EMPTY, LOGGING, FULL } LogMode;

typedef struct {
    LogMode mode;
    int count;
    int samples[SLOTS];
} Logger;

void log_step(Logger *g, int value) {
    switch (g->mode) {
        case EMPTY:
            g->samples[0] = value;
            g->count = 1;
            g->mode = LOGGING;
            break;
        case LOGGING:
            if (g->count < SLOTS) {
                g->samples[g->count] = value;
                g->count++;
            }
            if (g->count >= SLOTS) {
                g->mode = FULL;
            }
            break;
        case FULL:
            break;
    }
}
