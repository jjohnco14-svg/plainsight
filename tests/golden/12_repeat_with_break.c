int first_negative(const int *values, int n) {
    int found = -1;
    for (int i = 0; i < n; i++) {
        if (values[i] < 0) {
            found = i;
            break;
        }
    }
    return found;
}
