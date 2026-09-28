void reset(int *p) {
    *p = 0;
}
void reset_twice(int *p) {
    reset(p);
    reset(p);
}
