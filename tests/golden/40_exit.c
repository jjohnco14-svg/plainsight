#include <stdio.h>
#include <stdlib.h>
void must_be_positive(int v) {
    if (v <= 0) {
        fprintf(stderr, "bad value %d\n", v);
        exit(1);
    }
}
