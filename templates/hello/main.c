/*
 * __PROJECT__ - payload PS5 genere par Forge5.
 *
 * Tout ce qui est ecrit sur stdout/stderr remonte dans l'onglet
 * « Sortie PS5 » de Forge5 (via l'ELF loader).
 */
#include <stdio.h>
#include <unistd.h>

int main(void) {
    printf("Bonjour depuis la PS5 ! (__PROJECT__)\n");
    printf("PID du payload : %d\n", getpid());
    return 0;
}
