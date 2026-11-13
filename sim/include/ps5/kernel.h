/* Forge5 - mode sans PS5 : API noyau simulee (memes signatures que le SDK).
   Les lectures/ecritures dans la memoire du noyau n'existent pas sur PC :
   ces fonctions renvoient une erreur et l'indiquent dans le journal noyau. */
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <sys/types.h>
#include <sys/cdefs.h>
__BEGIN_DECLS
uint32_t kernel_get_fw_version(void);
int32_t kernel_copyin(const void *udaddr, intptr_t kaddr, size_t len);
int32_t kernel_copyout(intptr_t kaddr, void *udaddr, size_t len);
int32_t kernel_setlong(intptr_t addr, uint64_t val);
int32_t kernel_setint(intptr_t addr, uint32_t val);
int32_t kernel_setshort(intptr_t addr, uint16_t val);
int32_t kernel_setchar(intptr_t addr, uint8_t val);
uint64_t kernel_getlong(intptr_t addr);
uint32_t kernel_getint(intptr_t addr);
uint16_t kernel_getshort(intptr_t addr);
uint8_t  kernel_getchar(intptr_t addr);
intptr_t kernel_get_proc(pid_t pid);
intptr_t kernel_get_proc_thread(pid_t pid, pid_t tid);
intptr_t kernel_get_proc_ucred(pid_t pid);
intptr_t kernel_get_proc_filedesc(pid_t pid);
intptr_t kernel_get_proc_file(pid_t pid, int fd);
int kernel_proc_copyin(pid_t pid, const void *buf, intptr_t addr, size_t len);
int kernel_proc_copyout(pid_t pid, intptr_t addr, void *buf, size_t len);
uint64_t kernel_proc_getlong(pid_t pid, intptr_t addr);
uint32_t kernel_proc_getint(pid_t pid, intptr_t addr);
uint16_t kernel_proc_getshort(pid_t pid, intptr_t addr);
uint8_t  kernel_proc_getchar(pid_t pid, intptr_t addr);
int kernel_proc_setchar(pid_t pid, intptr_t addr, char val);
int kernel_proc_setshort(pid_t pid, intptr_t addr, short val);
int kernel_proc_setint(pid_t pid, intptr_t addr, int val);
int kernel_proc_setlong(pid_t pid, intptr_t addr, long val);
int kernel_get_vmem_protection(pid_t pid, intptr_t addr, size_t len);
int kernel_set_vmem_protection(pid_t pid, intptr_t addr, size_t len, int prot);
int kernel_mprotect(pid_t pid, intptr_t addr, size_t size, int prot);
int kernel_overlap_sockets(pid_t pid, int master_sock, int victim_sock);
int      kernel_dynlib_handle(pid_t pid, const char* basename, uint32_t *handle);
intptr_t kernel_dynlib_dlsym(pid_t pid, uint32_t handle, const char *sym);
intptr_t kernel_dynlib_resolve(pid_t pid, uint32_t handle, const char *nid);
intptr_t kernel_dynlib_mapbase_addr(pid_t pid, uint32_t handle);
intptr_t kernel_dynlib_entry_addr(pid_t pid, uint32_t handle);
intptr_t kernel_dynlib_init_addr(pid_t pid, uint32_t handle);
intptr_t kernel_dynlib_fini_addr(pid_t pid, uint32_t handle);
uint64_t kernel_get_ucred_authid(pid_t pid);
int32_t  kernel_set_ucred_authid(pid_t pid, uint64_t authid);
int32_t kernel_get_ucred_caps(pid_t pid, uint8_t caps[16]);
int32_t kernel_set_ucred_caps(pid_t pid, const uint8_t caps[16]);
int32_t kernel_get_ucred_attrs(pid_t pid, uint8_t attrs[32]);
int32_t kernel_set_ucred_attrs(pid_t pid, const uint8_t attrs[32]);
int32_t kernel_get_qaflags(uint8_t qaflags[16]);
int32_t kernel_set_qaflags(uint8_t const qaflags[16]);
intptr_t kernel_get_root_vnode(void);
intptr_t kernel_get_proc_rootdir(pid_t pid);
int32_t  kernel_set_proc_rootdir(pid_t pid, intptr_t vnode);
intptr_t kernel_get_proc_jaildir(pid_t pid);
int32_t  kernel_set_proc_jaildir(pid_t pid, intptr_t vnode);
uid_t   kernel_get_ucred_uid(pid_t pid);
int32_t kernel_set_ucred_uid(pid_t pid, uid_t uid);
uid_t   kernel_get_ucred_ruid(pid_t pid);
int32_t kernel_set_ucred_ruid(pid_t pid, uid_t ruid);
uid_t   kernel_get_ucred_svuid(pid_t pid);
int32_t kernel_set_ucred_svuid(pid_t pid, uid_t svuid);
gid_t   kernel_get_ucred_rgid(pid_t pid);
int32_t kernel_set_ucred_rgid(pid_t pid, gid_t rgid);
__END_DECLS
