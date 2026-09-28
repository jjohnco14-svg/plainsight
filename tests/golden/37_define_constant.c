#define MAX 10
int fill_count(void) {
    int n = 0;
    for (int i = 0; i < MAX; i++) {
        n += 2;
    }
    return n;
}
