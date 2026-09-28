typedef enum { OPEN, CLOSED, LOCKED } DoorMode;

typedef struct {
    DoorMode mode;
    DoorMode previous;
    int count;
} Door;

/* The second change reads the mode right after it changed: order matters. */
void close_door(Door *d) {
    switch (d->mode) {
        case OPEN:
            d->mode = CLOSED;
            d->previous = d->mode;
            break;
        case CLOSED:
        case LOCKED:
            break;
    }
}

/* OPEN falls through into CLOSED: not a simple mode table. */
void lock_door(Door *d) {
    switch (d->mode) {
        case OPEN:
            d->count++;
        case CLOSED:
            d->mode = LOCKED;
            break;
        case LOCKED:
            break;
    }
}

/* A local named like the record's field: names must not be shortened. */
int bump(Door *d) {
    int count = d->count;
    d->count = count + 1;
    return count;
}
