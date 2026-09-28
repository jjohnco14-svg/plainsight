int ends_line(const char *s) {
    char nl = '\n';
    const char *greeting = "hi there";
    if (s[0] == nl) {
        return 1;
    }
    return greeting[0] == 'h';
}
