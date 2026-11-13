/*
 * Forge5 - mode sans PS5.
 *
 * Imite sur PC (Linux / WSL) les fonctions qu'un homebrew PS5 appelle :
 * journal noyau, notifications, infos materiel, utilisateur, noyau.
 * Toutes les fonctions sont "faibles" (weak) : si ton code fournit sa propre
 * version, c'est la tienne qui est utilisee.
 *
 * Forge5 lit les messages speciaux "\x1e" "F5:" envoyes sur stderr pour
 * afficher les notifications et le journal noyau dans son interface.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/types.h>

#define WEAK __attribute__((weak))

/* ------------------------------------------------------------------ */
/* Canal vers Forge5                                                   */
/* ------------------------------------------------------------------ */
static void f5_send(const char *kind, const char *data) {
  /* Encode en hexadecimal pour garder un message sur une seule ligne */
  static const char hex[] = "0123456789abcdef";
  size_t n = strlen(data);
  char *buf = malloc(n * 2 + 1);
  if (!buf) return;
  for (size_t i = 0; i < n; i++) {
    buf[2 * i] = hex[(unsigned char)data[i] >> 4];
    buf[2 * i + 1] = hex[(unsigned char)data[i] & 15];
  }
  buf[n * 2] = 0;
  fprintf(stderr, "\x1e" "F5:%s:%s\n", kind, buf);
  fflush(stderr);
  free(buf);
}

static void f5_unsupported(const char *fn) {
  char msg[256];
  snprintf(msg, sizeof msg, "[SIM] %s() n'existe pas sur PC : renvoie une erreur. "
           "Teste cette partie sur une vraie PS5.", fn);
  f5_send("klog", msg);
}

__attribute__((constructor)) static void f5_init(void) {
  /* Sortie ligne par ligne, comme sur la console */
  setvbuf(stdout, NULL, _IOLBF, 0);
  f5_send("start", "1");
}

/* ------------------------------------------------------------------ */
/* <ps5/klog.h>                                                        */
/* ------------------------------------------------------------------ */
WEAK int klog_printf(const char *fmt, ...) {
  char buf[4096];
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(buf, sizeof buf, fmt, ap);
  va_end(ap);
  size_t len = strlen(buf);
  if (len && buf[len - 1] == '\n') buf[len - 1] = 0;
  f5_send("klog", buf);
  return n;
}

WEAK int klog_puts(const char *s) { return klog_printf("%s", s); }

WEAK int klog_perror(const char *s) { return klog_printf("%s: %s", s, strerror(errno)); }

/* ------------------------------------------------------------------ */
/* <ps5/payload.h>                                                     */
/* ------------------------------------------------------------------ */
typedef struct {
  void *sys_dynlib_dlsym; int *rwpipe; int *rwpair;
  intptr_t kpipe_addr; intptr_t kdata_base_addr; int *payloadout;
} f5_payload_args_t;
static int f5_payloadout;
static f5_payload_args_t f5_args = { .payloadout = &f5_payloadout };

WEAK void *payload_get_args(void) { return &f5_args; }
WEAK void payload_exit(int code) { exit(code); }

/* ------------------------------------------------------------------ */
/* Notifications                                                       */
/* ------------------------------------------------------------------ */
/* API recente (exemple "notify" du SDK) : JSON complet */
WEAK int sceNotificationSend(int userId, bool isLogged, const char *payload) {
  (void)userId; (void)isLogged;
  f5_send("notify", payload ? payload : "");
  return 0;
}

/* API classique : struct notify_request_t, message a l'offset 0x2D */
WEAK int sceKernelSendNotificationRequest(int device, void *req, size_t size, int blocking) {
  (void)device; (void)size; (void)blocking;
  if (!req) return -1;
  char msg[3076];
  strncpy(msg, (const char *)req + 0x2D, sizeof msg - 1);
  msg[sizeof msg - 1] = 0;
  f5_send("notify", msg);
  return 0;
}

/* ------------------------------------------------------------------ */
/* Materiel et systeme (valeurs realistes d'une PS5)                   */
/* ------------------------------------------------------------------ */
static int f5_env_int(const char *name, int def) {
  const char *v = getenv(name);
  return v ? (int)strtol(v, NULL, 0) : def;
}

WEAK int sceKernelGetHwModelName(char *s) { strcpy(s, "CFI-2016 (simule par Forge5)"); return 0; }
WEAK int sceKernelGetHwSerialNumber(char *s) { strcpy(s, "F5SIM0000001"); return 0; }
WEAK long sceKernelGetCpuFrequency(void) { return 3500L * 1000 * 1000; }

static int f5_temp(int base) {
  /* temperature qui varie doucement, comme une vraie console */
  return base + (int)(time(NULL) % 7) - 3;
}
WEAK int sceKernelGetCpuTemperature(int *t) { *t = f5_temp(f5_env_int("FORGE5_CPU_TEMP", 52)); return 0; }
WEAK int sceKernelGetSocSensorTemperature(int sensor, int *t) { (void)sensor; *t = f5_temp(f5_env_int("FORGE5_SOC_TEMP", 58)); return 0; }

WEAK int sceKernelUsleep(unsigned int us) { return usleep(us); }
WEAK int sceKernelSleep(unsigned int s) { return (int)sleep(s); }
WEAK uint64_t sceKernelGetProcessTime(void) {
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return (uint64_t)ts.tv_sec * 1000000ULL + (uint64_t)ts.tv_nsec / 1000ULL;
}

/* Utilisateur */
WEAK int sceUserServiceInitialize(void *params) { (void)params; return 0; }
WEAK int sceUserServiceGetForegroundUser(int *user) { *user = 0x10000001; return 0; }
WEAK int sceUserServiceGetUserName(int user, char *name, size_t size) {
  (void)user;
  snprintf(name, size, "%s", getenv("FORGE5_USER") ? getenv("FORGE5_USER") : "Joueur");
  return 0;
}

/* ------------------------------------------------------------------ */
/* <ps5/kernel.h> : version du firmware simulee, le reste n'existe pas */
/* ------------------------------------------------------------------ */
WEAK uint32_t kernel_get_fw_version(void) { return (uint32_t)f5_env_int("FORGE5_FW", 0x07610000); }

#define STUB(ret, name, params, value) \
  WEAK ret name params { f5_unsupported(#name); errno = ENOSYS; return value; }

STUB(int32_t, kernel_copyin, (const void *a, intptr_t b, size_t c), -1)
STUB(int32_t, kernel_copyout, (intptr_t a, void *b, size_t c), -1)
STUB(int32_t, kernel_setlong, (intptr_t a, uint64_t b), -1)
STUB(int32_t, kernel_setint, (intptr_t a, uint32_t b), -1)
STUB(int32_t, kernel_setshort, (intptr_t a, uint16_t b), -1)
STUB(int32_t, kernel_setchar, (intptr_t a, uint8_t b), -1)
STUB(uint64_t, kernel_getlong, (intptr_t a), 0)
STUB(uint32_t, kernel_getint, (intptr_t a), 0)
STUB(uint16_t, kernel_getshort, (intptr_t a), 0)
STUB(uint8_t, kernel_getchar, (intptr_t a), 0)
STUB(intptr_t, kernel_get_proc, (pid_t a), 0)
STUB(intptr_t, kernel_get_proc_thread, (pid_t a, pid_t b), 0)
STUB(intptr_t, kernel_get_proc_ucred, (pid_t a), 0)
STUB(intptr_t, kernel_get_proc_filedesc, (pid_t a), 0)
STUB(intptr_t, kernel_get_proc_file, (pid_t a, int b), 0)
STUB(int, kernel_proc_copyin, (pid_t a, const void *b, intptr_t c, size_t d), -1)
STUB(int, kernel_proc_copyout, (pid_t a, intptr_t b, void *c, size_t d), -1)
STUB(uint64_t, kernel_proc_getlong, (pid_t a, intptr_t b), 0)
STUB(uint32_t, kernel_proc_getint, (pid_t a, intptr_t b), 0)
STUB(uint16_t, kernel_proc_getshort, (pid_t a, intptr_t b), 0)
STUB(uint8_t, kernel_proc_getchar, (pid_t a, intptr_t b), 0)
STUB(int, kernel_proc_setchar, (pid_t a, intptr_t b, char c), -1)
STUB(int, kernel_proc_setshort, (pid_t a, intptr_t b, short c), -1)
STUB(int, kernel_proc_setint, (pid_t a, intptr_t b, int c), -1)
STUB(int, kernel_proc_setlong, (pid_t a, intptr_t b, long c), -1)
STUB(int, kernel_get_vmem_protection, (pid_t a, intptr_t b, size_t c), -1)
STUB(int, kernel_set_vmem_protection, (pid_t a, intptr_t b, size_t c, int d), -1)
STUB(int, kernel_mprotect, (pid_t a, intptr_t b, size_t c, int d), -1)
STUB(int, kernel_overlap_sockets, (pid_t a, int b, int c), -1)
STUB(int, kernel_dynlib_handle, (pid_t a, const char *b, uint32_t *c), -1)
STUB(intptr_t, kernel_dynlib_dlsym, (pid_t a, uint32_t b, const char *c), 0)
STUB(intptr_t, kernel_dynlib_resolve, (pid_t a, uint32_t b, const char *c), 0)
STUB(intptr_t, kernel_dynlib_mapbase_addr, (pid_t a, uint32_t b), 0)
STUB(intptr_t, kernel_dynlib_entry_addr, (pid_t a, uint32_t b), 0)
STUB(intptr_t, kernel_dynlib_init_addr, (pid_t a, uint32_t b), 0)
STUB(intptr_t, kernel_dynlib_fini_addr, (pid_t a, uint32_t b), 0)
STUB(uint64_t, kernel_get_ucred_authid, (pid_t a), 0)
STUB(int32_t, kernel_set_ucred_authid, (pid_t a, uint64_t b), -1)
STUB(int32_t, kernel_get_ucred_caps, (pid_t a, uint8_t b[16]), -1)
STUB(int32_t, kernel_set_ucred_caps, (pid_t a, const uint8_t b[16]), -1)
STUB(int32_t, kernel_get_ucred_attrs, (pid_t a, uint8_t b[32]), -1)
STUB(int32_t, kernel_set_ucred_attrs, (pid_t a, const uint8_t b[32]), -1)
STUB(int32_t, kernel_get_qaflags, (uint8_t a[16]), -1)
STUB(int32_t, kernel_set_qaflags, (uint8_t const a[16]), -1)
STUB(intptr_t, kernel_get_root_vnode, (void), 0)
STUB(intptr_t, kernel_get_proc_rootdir, (pid_t a), 0)
STUB(int32_t, kernel_set_proc_rootdir, (pid_t a, intptr_t b), -1)
STUB(intptr_t, kernel_get_proc_jaildir, (pid_t a), 0)
STUB(int32_t, kernel_set_proc_jaildir, (pid_t a, intptr_t b), -1)
STUB(uid_t, kernel_get_ucred_uid, (pid_t a), (uid_t)-1)
STUB(int32_t, kernel_set_ucred_uid, (pid_t a, uid_t b), -1)
STUB(uid_t, kernel_get_ucred_ruid, (pid_t a), (uid_t)-1)
STUB(int32_t, kernel_set_ucred_ruid, (pid_t a, uid_t b), -1)
STUB(uid_t, kernel_get_ucred_svuid, (pid_t a), (uid_t)-1)
STUB(int32_t, kernel_set_ucred_svuid, (pid_t a, uid_t b), -1)
STUB(gid_t, kernel_get_ucred_rgid, (pid_t a), (gid_t)-1)
STUB(int32_t, kernel_set_ucred_rgid, (pid_t a, gid_t b), -1)
