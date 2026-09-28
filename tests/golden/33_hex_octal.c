unsigned int masks(unsigned int v) {
    unsigned int low = v & 0x1F;
    unsigned int mode = 017;
    return (low << 2) | mode;
}
