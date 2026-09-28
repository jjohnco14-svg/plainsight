#include <stdbool.h>

#define GREEN_TIME 30
#define YELLOW_TIME 5
#define RED_TIME 20
#define MIN_GREEN 10

typedef enum { GREEN, YELLOW, RED } Light;

typedef struct {
    Light light;
    int timer;
} Crossing;

/* Called once per second. */
void crossing_tick(Crossing *c, bool button_pressed) {
    switch (c->light) {
        case GREEN:
            c->timer++;
            if (c->timer >= GREEN_TIME || (button_pressed && c->timer >= MIN_GREEN)) {
                c->light = YELLOW;
                c->timer = 0;
            }
            break;
        case YELLOW:
            c->timer++;
            if (c->timer >= YELLOW_TIME) {
                c->light = RED;
                c->timer = 0;
            }
            break;
        case RED:
            c->timer++;
            if (c->timer >= RED_TIME) {
                c->light = GREEN;
                c->timer = 0;
            }
            break;
    }
}

int count_rush_hours(const int *cars, int hours) {
    int rush = 0;
    for (int h = 0; h < hours; h++) {
        if (cars[h] > 100) {
            rush++;
        }
    }
    return rush;
}
