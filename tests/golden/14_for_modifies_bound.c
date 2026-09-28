int shrink(int n) {
    int rounds = 0;
    for (int i = 0; i < n; i++) {
        n--;
        rounds++;
    }
    return rounds;
}
