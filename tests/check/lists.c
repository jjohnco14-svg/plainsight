#include <stdio.h>

int minutes_to_fill(const int *levels, int n) {
    int start = -1;
    for (int i = 0; i < n; i++) {
        if (start < 0 && levels[i] < 20) start = i;
        if (start >= 0 && levels[i] >= 90) return i - start;
    }
    return -1;
}

void clamp_all(double *values, int n, double lo, double hi) {
    for (int i = 0; i < n; i++) {
        if (values[i] < lo) values[i] = lo;
        else if (values[i] > hi) values[i] = hi;
    }
}

int greetings = 0;

void greet(const char *name) {
    greetings++;
    printf("Hello, %s!\n", name);
}
