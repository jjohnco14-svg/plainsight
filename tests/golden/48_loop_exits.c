#include <stdlib.h>

int find(const int *a, int n, int x) {
    for (int i = 0; i < n; i++) {
        if (a[i] == x) {
            return i;
        }
    }
    return -1;
}

void check_all(const int *a, int n) {
    for (int i = 0; i < n; i++) {
        if (a[i] < 0) {
            exit(1);
        }
    }
}

int first_code(const int *a, int n) {
    for (int i = 0; i < n; i++) {
        switch (a[i]) {
            case 7:
                return i;
            default:
                break;
        }
    }
    return -1;
}

int count_rows(int rows, int cols) {
    int seen = 0;
    for (int r = 0; r < rows; r++) {
        for (int c = 0; c < cols; c++) {
            if (c == 2) {
                break;
            }
            seen++;
        }
    }
    return seen;
}

void pick(int *out, int big) {
    *out = big ? 100 : 1;
}
