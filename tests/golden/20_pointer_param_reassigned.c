void maybe_write(int *p, int *q, int use_q) {
    if (use_q) {
        p = q;
    }
    *p = 1;
}
