#include <ctype.h>

#define IGNORE_ARG(x) 42

enum Level { LOW = 1, MID, HIGH = 10 };

int classify(int code) {
    int score = 0;
    switch (code) {
        case 0:
        case 1:
            score = 5;
            break;
        case 2:
            score = 7;
            /* falls through on purpose */
        case 3:
            score += 1;
            if (score > 7) {
                break;
            }
            score = 0;
            break;
        default:
            score = -1;
            break;
    }
    return score;
}

int count_high(const int *levels, int n) {
    int highs = 0;
    for (int i = 0; i < n; i++) {
        switch (levels[i]) {
            case HIGH:
                highs++;
                break;
            case LOW:
                continue;
        }
    }
    return highs;
}

int letters_first(const char *s) {
    return isalpha(s[0]) + IGNORE_ARG(s);
}
