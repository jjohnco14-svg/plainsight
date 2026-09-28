int digits(int n) {
    int d = 0;
    do {
        n = n / 10;
        d++;
    } while (n != 0);
    return d;
}
