#include <stdio.h>
#include <stdbool.h>

#define CAP 4

typedef struct {
    int items[CAP];
    int head;
    int count;
} RingBuf;

void rb_init(RingBuf *rb) {
    rb->head = 0;
    rb->count = 0;
}

bool rb_push(RingBuf *rb, int value) {
    if (rb->count == CAP) {
        return false;
    }
    int tail = (rb->head + rb->count) % CAP;
    rb->items[tail] = value;
    rb->count++;
    return true;
}

bool rb_pop(RingBuf *rb, int *out) {
    if (rb->count == 0) {
        return false;
    }
    *out = rb->items[rb->head];
    rb->head = (rb->head + 1) % CAP;
    rb->count--;
    return true;
}

int main(void) {
    RingBuf rb;
    rb_init(&rb);
    for (int v = 1; v <= 5; v++) {
        if (!rb_push(&rb, v)) {
            printf("push %d refused\n", v);
        }
    }
    int x;
    rb_pop(&rb, &x);
    printf("%d ", x);
    rb_pop(&rb, &x);
    printf("%d ", x);
    rb_push(&rb, 6);
    while (rb_pop(&rb, &x)) {
        printf("%d ", x);
    }
    printf("\n");
    return 0;
}
