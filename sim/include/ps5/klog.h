/* Forge5 - mode sans PS5 : journal noyau simule (meme API que le SDK). */
#pragma once
#include <sys/cdefs.h>
__BEGIN_DECLS
int klog_printf(const char *fmt, ...);
int klog_puts(const char *s);
int klog_perror(const char *s);
__END_DECLS
