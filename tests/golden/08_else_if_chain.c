int grade(int score) {
    if (score >= 90) {
        return 4;
    } else if (score >= 80) {
        return 3;
    } else if (score >= 70) {
        return 2;
    }
    return 0;
}
