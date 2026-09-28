#include <limits.h>

#define LOOKUP(i) (codes[(i)])
#define FLAG_A 010
#define FLAG_B 0x20

typedef void (Callback)(int);

int codes[4] = {3, 1, 4, 1};
int flags = 0;

int decode(int i) {
    return LOOKUP(i);
}

void set_flags(void) {
    flags = FLAG_A | FLAG_B;
}

int half_max(void) {
    return INT_MAX / 2;
}

void run_callback(Callback *cb, int v) {
    cb(v);
}
