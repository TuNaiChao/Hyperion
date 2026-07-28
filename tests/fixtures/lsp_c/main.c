#include <stdio.h>
#include "lib.h"

int main(void) {
    int x = add(2, 3);
    int y = multiply(4, 5);
    int z = add(x, y);
    printf("%d %d %d\n", x, y, z);
    return 0;
}
