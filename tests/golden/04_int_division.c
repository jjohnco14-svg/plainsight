double divide(int a, int b, double x, double y) {
    int q = a / b;
    int r = a % b;
    double d = x / y;
    return q + r + d;
}
