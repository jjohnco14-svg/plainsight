typedef enum { WAIT, RUN, ALARM } Mode;

typedef struct {
    Mode mode;
    int ticks;
} Machine;

static int alarms = 0;

void sound_alarm(void) {
    alarms++;
}

int sensor_ok(int reading) {
    return reading < 100;
}

/* A mode machine whose RUN mode runs another function. */
void machine_step(Machine *m, int reading) {
    switch (m->mode) {
        case WAIT:
            if (reading > 0) {
                m->mode = RUN;
            }
            break;
        case RUN:
            if (reading >= 100) {
                sound_alarm();
                m->mode = ALARM;
            }
            break;
        case ALARM:
            break;
    }
}

/* sensor_ok is run only inside the condition. */
int check(int reading) {
    if (sensor_ok(reading)) {
        return 1;
    }
    return 0;
}
