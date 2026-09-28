struct Pair {
    int a;
    int b;
};

/** Lookup table, documented with a comment right before it. */
static const int table[4] = { 7, 8, 9, 10 };

int partial(void) {
    unsigned char buf[8] = {0};
    int some[3] = {1, 2};
    struct Pair p = {.a = 5};
    return buf[0] + some[2] + p.b + table[1];
}
