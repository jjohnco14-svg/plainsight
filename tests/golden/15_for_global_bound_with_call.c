int limit = 10;
void tick(void) {
    limit = limit - 1;
}
void run(void) {
    for (int i = 0; i < limit; i++) {
        tick();
    }
}
