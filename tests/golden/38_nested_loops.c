int find_pair(const int *a, int n, int target) {
    int hits = 0;
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < n; j++) {
            if (a[i] + a[j] == target) {
                hits++;
                break;
            }
        }
    }
    return hits;
}
