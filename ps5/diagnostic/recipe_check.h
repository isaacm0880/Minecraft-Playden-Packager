#ifndef PS5_RECIPE_CHECK_H
#define PS5_RECIPE_CHECK_H
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#define RECIPE_MAX 16384
typedef struct {const char *s; size_t n,p; int depth;} J;
static void skipws(J *j){while(j->p<j->n && (j->s[j->p]==' '||j->s[j->p]=='\n'||j->s[j->p]=='\r'||j->s[j->p]=='\t'))++j->p;}
static int stringval(J *j,size_t *start,size_t *end){
 skipws(j);if(j->p>=j->n||j->s[j->p++]!='"')return 0;*start=j->p;
 while(j->p<j->n){unsigned char c=(unsigned char)j->s[j->p++];
  if(c=='"'){*end=j->p-1;return 1;}if(c<32)return 0;
  if(c=='\\'){if(j->p>=j->n)return 0;c=(unsigned char)j->s[j->p++];
   if(strchr("\"\\/bfnrt",c))continue;
   if(c!='u'||j->n-j->p<4)return 0;
   for(int i=0;i<4;i++)if(!isxdigit((unsigned char)j->s[j->p++]))return 0;
  }
 }return 0;
}
static int value(J *j);
static int object(J *j){
 if(++j->depth>32)return 0;
 ++j->p;skipws(j);
 if(j->p<j->n&&j->s[j->p]=='}'){++j->p;--j->depth;return 1;}
 for(;;){size_t a,b;if(!stringval(j,&a,&b))return 0;skipws(j);
  if(j->p>=j->n||j->s[j->p++]!=':')return 0;
  if(!value(j))return 0;
  skipws(j);if(j->p>=j->n)return 0;
  char c=j->s[j->p++];if(c=='}'){--j->depth;return 1;}if(c!=',')return 0;
 }
}
static int array(J *j){
 if(++j->depth>32)return 0;
 ++j->p;skipws(j);
 if(j->p<j->n&&j->s[j->p]==']'){++j->p;--j->depth;return 1;}
 for(;;){if(!value(j))return 0;
  skipws(j);if(j->p>=j->n)return 0;
  char c=j->s[j->p++];if(c==']'){--j->depth;return 1;}if(c!=',')return 0;}
}
static int value(J *j){
 skipws(j);if(j->p>=j->n)return 0;char c=j->s[j->p];
 if(c=='{')return object(j);
 if(c=='[')return array(j);
 if(c=='"'){size_t a,b;return stringval(j,&a,&b);}
 const char *lit=c=='t'?"true":c=='f'?"false":c=='n'?"null":NULL;
 if(lit){size_t n=strlen(lit);if(j->n-j->p<n||memcmp(j->s+j->p,lit,n))return 0;j->p+=n;return 1;}
 if(c=='-')++j->p;
 if(j->p>=j->n)return 0;
 if(j->s[j->p]=='0')++j->p;
 else if(j->s[j->p]>='1'&&j->s[j->p]<='9'){do{++j->p;}while(j->p<j->n&&isdigit((unsigned char)j->s[j->p]));}
 else return 0;
 if(j->p<j->n&&j->s[j->p]=='.'){++j->p;size_t a=j->p;while(j->p<j->n&&isdigit((unsigned char)j->s[j->p]))++j->p;if(a==j->p)return 0;}
 if(j->p<j->n&&(j->s[j->p]=='e'||j->s[j->p]=='E')){++j->p;if(j->p<j->n&&(j->s[j->p]=='+'||j->s[j->p]=='-'))++j->p;size_t a=j->p;while(j->p<j->n&&isdigit((unsigned char)j->s[j->p]))++j->p;if(a==j->p)return 0;}
 return 1;
}
static int keyval(const char *s,size_t n,const char *key,const char **out,size_t *len){
 J j={s,n,0,0};skipws(&j);if(j.p>=n||s[j.p++]!='{')return 0;skipws(&j);
 while(j.p<n&&s[j.p]!='}'){size_t a,b;if(!stringval(&j,&a,&b))return 0;skipws(&j);
  if(j.p>=n||s[j.p++]!=':')return 0;
  skipws(&j);size_t p=j.p;
  if(!value(&j))return 0;
  if(strlen(key)==b-a&&!memcmp(s+a,key,b-a)){*out=s+p;*len=j.p-p;return 1;}
  skipws(&j);if(j.p>=n||s[j.p++]!=',')break;skipws(&j);
 }return 0;
}
static int field(const char *s,size_t n,const char *key,const char *expected){
 const char *v;size_t z;return keyval(s,n,key,&v,&z)&&z==strlen(expected)&&!memcmp(v,expected,z);
}
static int nested(const char *s,size_t n,const char *parent,const char *key,const char *expected){
 const char *v;size_t z;return keyval(s,n,parent,&v,&z)&&field(v,z,key,expected);
}
/* A Java-8 command is data, like the existing @playden.args. It must match
   the immutable command sidecar, use our fixed classpath adapter, and fit the
   actual Playden splitter. No shell, alternate executable or cwd is allowed. */
static int java8_command(const char *folder,char *json,size_t cap){
 char path[1024],command[4097];struct stat st;
 int n=snprintf(path,sizeof(path),"%s/playden.java8-command",folder);
 if(n<0||(size_t)n>=sizeof(path))return 0;
 int fd=open(path,O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK);
 if(fd<0)return 0;
 if(fstat(fd,&st)||!S_ISREG(st.st_mode)||st.st_size<1||st.st_size>4096){close(fd);return 0;}
 size_t used=0;
 while(used<sizeof(command)){
  ssize_t got=read(fd,command+used,sizeof(command)-used);
  if(got<0){if(errno==EINTR)continue;close(fd);return 0;}if(!got)break;used+=(size_t)got;
 }
 close(fd);if(!used||used>4096)return 0;command[used]=0;
 for(size_t i=0;i<used;i++)if((unsigned char)command[i]<32||command[i]==127)return 0;
 const char *tail="\"org.minecraftplayden.Java8Launch\"";size_t tail_len=strlen(tail);
 if(used<tail_len||strcmp(command+used-tail_len,tail))return 0;
 if(!strstr(command,"\"-cp\" \"../../playden-java8.jar\"")&&
    !strstr(command,"\"-classpath\" \"../../playden-java8.jar\""))return 0;
 size_t words=0,p=0;
 while(p<used){
  if(command[p++]!='\"'||++words>32)return 0;
  while(p<used&&command[p]!='\"')p++;
  if(p==used)return 0;
  p++;
  if(p<used&&command[p++]!=' ')return 0;
 }
 size_t z=0;if(cap<3)return 0;json[z++]='\"';
 for(size_t i=0;i<used;i++){
  if(z+3>=cap)return 0;
  if(command[i]=='\\'||command[i]=='\"')json[z++]='\\';
  json[z++]=command[i];
 }
 json[z++]='\"';json[z]=0;return 1;
}
/* New metadata layout, with a strict unambiguous legacy fallback. */
static const char *recipe_name(const char *folder){
 char path[1024];struct stat metadata,current,legacy;
 int n=snprintf(path,sizeof(path),"%s/playden",folder);
 if(n<0||(size_t)n>=sizeof(path))return NULL;
 if(lstat(path,&metadata)==0){if(!S_ISDIR(metadata.st_mode)||S_ISLNK(metadata.st_mode))return NULL;}
 else if(errno!=ENOENT)return NULL;
 n=snprintf(path,sizeof(path),"%s/playden/recipe.json",folder);
 if(n<0||(size_t)n>=sizeof(path))return NULL;
 int modern=lstat(path,&current)==0;
 if(!modern&&errno!=ENOENT)return NULL;
 n=snprintf(path,sizeof(path),"%s/recipe.json",folder);
 if(n<0||(size_t)n>=sizeof(path))return NULL;
 int old=lstat(path,&legacy)==0;
 if(!old&&errno!=ENOENT)return NULL;
 if(modern&&old)return NULL;
 if(modern)return S_ISREG(current.st_mode)&&!S_ISLNK(current.st_mode)?"playden/recipe.json":NULL;
 return old&&S_ISREG(legacy.st_mode)&&!S_ISLNK(legacy.st_mode)?"recipe.json":NULL;
}
static int recipe_check(const char *folder,char *reason,size_t cap){
 char path[1024];struct stat st;
 const char *name=recipe_name(folder);
 if(!name){snprintf(reason,cap,"recipe-path-invalid");return 0;}
 int n=snprintf(path,sizeof(path),"%s/%s",folder,name);
 if(n<0||(size_t)n>=sizeof(path)){snprintf(reason,cap,"path-too-long");return 0;}
 /* Open without following the final symlink, then inspect the opened inode.
    This avoids the lstat/fopen time-of-check/time-of-use race. */
 int fd=open(path,O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK);
 if(fd<0){snprintf(reason,cap,"recipe-unreadable");return 0;}
 if(fstat(fd,&st)!=0||!S_ISREG(st.st_mode)){
   close(fd);snprintf(reason,cap,"recipe-missing");return 0;
 }
 if(st.st_size<=0||st.st_size>RECIPE_MAX){
   close(fd);snprintf(reason,cap,"recipe-size-invalid");return 0;
 }
 char buf[RECIPE_MAX+1];size_t z=0;
 while(z<sizeof(buf)){
   ssize_t got=read(fd,buf+z,sizeof(buf)-z);
   if(got<0){if(errno==EINTR)continue;close(fd);snprintf(reason,cap,"recipe-read-failed");return 0;}
   if(got==0)break;
   z+=(size_t)got;
 }
 close(fd);
 if(z==0||z>RECIPE_MAX){snprintf(reason,cap,"recipe-read-failed");return 0;}
 J j={buf,z,0,0};skipws(&j);if(j.p>=z||buf[j.p]!='{'||!value(&j)){snprintf(reason,cap,"recipe-invalid-json");return 0;}
 skipws(&j);if(j.p!=z){snprintf(reason,cap,"recipe-invalid-json");return 0;}
 if(!field(buf,z,"recipe_version","1")||!field(buf,z,"architecture","\"pe64\"")||
    !field(buf,z,"graphics","\"opengl\"")||!field(buf,z,"executable","\"java/bin/java.exe\"")||
    !nested(buf,z,"game","store","\"imported\"")||
    !nested(buf,z,"community","format_version","1")){
   snprintf(reason,cap,"recipe-contract-mismatch");return 0;}
 const char *v;size_t vn;
 if(!keyval(buf,z,"launch_entries",&v,&vn)||vn<2||v[0]!='['){snprintf(reason,cap,"recipe-launch-missing");return 0;}
 J a={v,vn,1,0};skipws(&a);size_t start=a.p;
 if(a.p>=vn||v[a.p]!='{'||!value(&a)){snprintf(reason,cap,"recipe-launch-invalid");return 0;}
 char java8_json[8195];
 int modern=field(v+start,a.p-start,"arguments","\"@../../playden.args\"");
 int legacy=!modern&&java8_command(folder,java8_json,sizeof(java8_json))&&field(v+start,a.p-start,"arguments",java8_json);
 if(!field(v+start,a.p-start,"executable","\"java/bin/java.exe\"")||(!modern&&!legacy)){
   snprintf(reason,cap,"recipe-launch-mismatch");return 0;}
 snprintf(reason,cap,"ok");return 1;
}
#endif
