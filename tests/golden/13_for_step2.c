int sum_even(int n) {
    int total = 0;
    for (int i = 0; i < n; i += 2) {
        total += i;
    }
    return total;
}
