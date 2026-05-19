#include <stdio.h>

int main(void) {
    fprintf(stderr,
            "pdocker-direct: process-exec is not implemented for this Android ABI yet. "
            "This binary is packaged only to make ABI support explicit; use arm64-v8a "
            "for the current direct runtime.\n");
    return 126;
}
