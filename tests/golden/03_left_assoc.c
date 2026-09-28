int chain(int a, int b, int c) {
    int left = a - b - c;
    int right = a - (b - c);
    return left + right;
}
