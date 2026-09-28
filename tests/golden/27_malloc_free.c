#include <stdlib.h>
int scratch(int n) {
    int *buf = malloc(sizeof(int) * n);
    if (buf == NULL) {
        return -1;
    }
    buf[0] = n;
    int first = buf[0];
    free(buf);
    return first;
}
