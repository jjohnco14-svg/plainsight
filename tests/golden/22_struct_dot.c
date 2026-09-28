typedef struct {
    int x;
    int y;
} Point;

int make_x(int v) {
    Point p;
    p.x = v;
    p.y = 0;
    return p.x + p.y;
}
