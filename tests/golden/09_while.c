int count_down(int n) {
    int steps = 0;
    while (n > 0) {
        n = n - 1;
        steps++;
    }
    return steps;
}
