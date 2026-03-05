/* scores.c — simple hash-table score tracker.
 *
 * Looks correct.  Contains one subtle bug: table_get() can return NULL
 * (key not found), but the caller in main() never checks for that and
 * dereferences the pointer unconditionally.  The crash happens on the
 * second iteration when "dan" is not in the table.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define BUCKETS 8

typedef struct Entry {
    char         *key;
    int           value;
    struct Entry *next;
} Entry;

typedef struct {
    Entry *buckets[BUCKETS];
} Table;

static unsigned hash(const char *s) {
    unsigned h = 5381;
    while (*s) h = h * 33 ^ (unsigned char)*s++;
    return h % BUCKETS;
}

void table_set(Table *t, const char *key, int value) {
    unsigned b = hash(key);
    Entry *e = t->buckets[b];
    while (e) {
        if (strcmp(e->key, key) == 0) { e->value = value; return; }
        e = e->next;
    }
    e        = malloc(sizeof(Entry));
    e->key   = strdup(key);
    e->value = value;
    e->next  = t->buckets[b];
    t->buckets[b] = e;
}

/* Returns NULL when the key is not in the table. */
Entry *table_get(Table *t, const char *key) {
    unsigned b = hash(key);
    Entry *e = t->buckets[b];
    while (e) {
        if (strcmp(e->key, key) == 0) return e;
        e = e->next;
    }
    return NULL;   /* key not found */
}

int main(void) {
    Table scores = {{NULL}};

    table_set(&scores, "alice", 92);
    table_set(&scores, "bob",   87);
    table_set(&scores, "carol", 95);

    /* Three names to look up — "dan" was never inserted. */
    const char *names[] = {"alice", "dan", "carol"};
    int total = 0;

    for (int i = 0; i < 3; i++) {
        Entry *e = table_get(&scores, names[i]);
        /* BUG: no NULL check.  When names[i] == "dan", e is NULL and
           the next line dereferences it → SIGSEGV. */
        total += e->value;
        printf("%s: %d\n", names[i], e->value);
    }

    printf("Total: %d\n", total);
    return 0;
}
