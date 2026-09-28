#include <stdio.h>
#include <ctype.h>

int main(void) {
    int c;
    int lines = 0, words = 0, longest = 0, current = 0;
    while ((c = getchar()) != EOF) {
        if (c == '\n') {
            lines++;
        }
        if (isspace(c)) {
            if (current > 0) {
                words++;
                if (current > longest) {
                    longest = current;
                }
                current = 0;
            }
        } else {
            current++;
        }
    }
    if (current > 0) {
        words++;
        if (current > longest) {
            longest = current;
        }
    }
    printf("lines: %d\nwords: %d\nlongest word: %d\n", lines, words, longest);
    return 0;
}
