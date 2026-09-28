int graded = 0;

int grade(int score, int bonus) {
    int total = score + bonus;
    graded++;
    if (total >= 90) {
        return 4;
    } else if (total >= 80) {
        return 3;
    } else if (total >= 70) {
        return 2;
    }
    return 0;
}
