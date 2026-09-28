#include <stddef.h>
size_t bytes_needed(size_t n) {
    int sample = 0;
    return n * sizeof(int) + sizeof(sample);
}
