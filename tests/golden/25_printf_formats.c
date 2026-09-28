#include <stdio.h>
void report(int n, double avg, const char *name, char grade, unsigned int mask) {
    printf("%d items\n", n);
    printf("average %.2f\n", avg);
    printf("name: %s, grade %c\n", name, grade);
    printf("mask %x", mask);
    printf("[%5d] 100%%\n", n);
    printf("raw %f\n", avg);
}
