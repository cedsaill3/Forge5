/* Forge5 - mode sans PS5 : arguments de payload simules. */
#pragma once
#include <stdint.h>
#include <sys/cdefs.h>
__BEGIN_DECLS
typedef struct payload_args {
  void *sys_dynlib_dlsym;
  int *rwpipe;
  int *rwpair;
  intptr_t kpipe_addr;
  intptr_t kdata_base_addr;
  int *payloadout;
} payload_args_t;
payload_args_t* payload_get_args(void);
void payload_exit(int exit_code);
__END_DECLS
