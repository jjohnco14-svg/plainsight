int next_id(void) {
    static int count = 0;
    count++;
    return count;
}
