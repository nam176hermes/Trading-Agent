#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sysexits.h>


int main(int argc, char *argv[])
{
    if ((argc == 2) && (strcmp(argv[1], "--version") == 0)) {
        (void)fputs(
            "package6-custodian retired-v2-supervisor-required\n",
            stdout);
        return EXIT_SUCCESS;
    }
    (void)fputs(
        "package6-custodian: release authority v2 supervisor required\n",
        stderr);
    return EX_CONFIG;
}
