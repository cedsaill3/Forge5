/*
 * __PROJECT__ - demo PS5 generee par Forge5.
 *
 * Fonctionne sur une vraie PS5 ET en mode « 💻 PC (sans PS5) » :
 * Forge5 simule les fonctions de la console sur ton PC.
 */
#include <stdio.h>
#include <string.h>
#include <stdbool.h>
#include <unistd.h>
#include <ps5/klog.h>
#include <ps5/kernel.h>

/* Fonctions systeme de la PS5 (fournies par la console, simulees sur PC) */
int  sceKernelGetHwModelName(char *);
int  sceKernelGetCpuTemperature(int *);
long sceKernelGetCpuFrequency(void);
int  sceNotificationSend(int userId, bool isLogged, const char *payload);

static void notifier(const char *message) {
    char json[512];
    snprintf(json, sizeof json,
             "{\"rawData\":{\"viewTemplateType\":\"InteractiveToastTemplateB\","
             "\"channelType\":\"Downloads\",\"useCaseId\":\"IDC\",\"isImmediate\":true,"
             "\"viewData\":{\"message\":{\"body\":\"%s\"},"
             "\"subMessage\":{\"body\":\"__PROJECT__\"}}}}", message);
    sceNotificationSend(0xFE, true, json);
}

int main(void) {
    char modele[128] = "";
    int temp = 0;
    uint32_t fw = kernel_get_fw_version();

    sceKernelGetHwModelName(modele);
    printf("Console   : %s\n", modele);
    printf("Firmware  : %x.%02x\n", (fw >> 24) & 0xff, (fw >> 16) & 0xff);
    printf("CPU       : %ld MHz\n", sceKernelGetCpuFrequency() / 1000000);

    for (int i = 1; i <= 3; i++) {
        sceKernelGetCpuTemperature(&temp);
        printf("Mesure %d  : CPU a %d °C\n", i, temp);
        klog_printf("__PROJECT__: mesure %d, %d C\n", i, temp);
        sleep(1);
    }

    notifier("Bonjour depuis mon homebrew !");
    /* La PS5 traite la notification en arriere-plan : on lui laisse le temps
       de finir avant de quitter, sinon le payload peut planter en sortant. */
    sleep(2);
    return 0;
}
