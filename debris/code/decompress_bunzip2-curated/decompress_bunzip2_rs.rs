#![deny(unsafe_op_in_unsafe_fn)]
#![allow(
    clippy::missing_safety_doc,
    dead_code,
    non_camel_case_types,
    non_snake_case,
    non_upper_case_globals,
    unused_assignments,
    unused_mut
)]
use ::core::arch::asm;

// `task_struct`/`mmap_action`/`signal_struct`/`sched_dl_entity` carry (or
// transitively contain by-value) c2rust_bitfields::BitfieldStruct derives
// (proc-macro crate unavailable in this build) but are used only by-pointer
// or not at all in this TU's live function bodies (get_bits..bunzip2) —
// grep-confirmed zero field-level references to any of the 4 names
// anywhere past the header-generated section (only `*mut task_struct` on
// the fabricated, since-deleted `riscv_current_is_tp` static, itself dead).
// Opaqued rather than derive-stripped, per the standard idiom already used
// in klist_rs.rs/objpool_rs.rs/glob_rs.rs/seq_buf_rs.rs/fonts_rs.rs.
macro_rules! opaque_marker {
    ($($name:ident),* $(,)?) => {
        $(
            #[repr(C)]
            #[derive(Copy, Clone)]
            pub struct $name {
                _private: [u8; 0],
            }
        )*
    };
}
opaque_marker!(task_struct, mmap_action, signal_struct, sched_dl_entity);

extern "C" {
    fn memset(
        _: *mut ::core::ffi::c_void,
        _: ::core::ffi::c_int,
        _: size_t,
    ) -> *mut ::core::ffi::c_void;
    fn kfree(objp: *const ::core::ffi::c_void);
    static mut kmalloc_caches: [kmem_buckets; 1];
    fn __kmalloc_noprof(size: size_t, flags: gfp_t) -> *mut ::core::ffi::c_void;
    fn __kmalloc_cache_noprof(
        s: *mut kmem_cache,
        flags: gfp_t,
        size: size_t,
    ) -> *mut ::core::ffi::c_void;
    fn __kmalloc_large_noprof(size: size_t, flags: gfp_t) -> *mut ::core::ffi::c_void;
    fn vmalloc_noprof(size: ::core::ffi::c_ulong) -> *mut ::core::ffi::c_void;
    fn vfree(addr: *const ::core::ffi::c_void);
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct bunzip_data {
    pub writeCopies: ::core::ffi::c_int,
    pub writePos: ::core::ffi::c_int,
    pub writeRunCountdown: ::core::ffi::c_int,
    pub writeCount: ::core::ffi::c_int,
    pub writeCurrent: ::core::ffi::c_int,
    pub fill: Option<
        unsafe extern "C" fn(*mut ::core::ffi::c_void, ::core::ffi::c_ulong) -> ::core::ffi::c_long,
    >,
    pub inbufCount: ::core::ffi::c_long,
    pub inbufPos: ::core::ffi::c_long,
    pub inbuf: *mut ::core::ffi::c_uchar,
    pub inbufBitCount: ::core::ffi::c_uint,
    pub inbufBits: ::core::ffi::c_uint,
    pub crc32Table: [::core::ffi::c_uint; 256],
    pub headerCRC: ::core::ffi::c_uint,
    pub totalCRC: ::core::ffi::c_uint,
    pub writeCRC: ::core::ffi::c_uint,
    pub dbuf: *mut ::core::ffi::c_uint,
    pub dbufSize: ::core::ffi::c_uint,
    pub selectors: [::core::ffi::c_uchar; 32768],
    pub groups: [group_data; 6],
    pub io_error: ::core::ffi::c_int,
    pub byteCount: [::core::ffi::c_int; 256],
    pub symToByte: [::core::ffi::c_uchar; 256],
    pub mtfSymbol: [::core::ffi::c_uchar; 256],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct group_data {
    pub limit: [::core::ffi::c_int; 21],
    pub base: [::core::ffi::c_int; 20],
    pub permute: [::core::ffi::c_int; 258],
    pub minLen: ::core::ffi::c_int,
    pub maxLen: ::core::ffi::c_int,
}
#[derive(Copy, Clone)]
#[repr(C, align(8))]
pub struct alloc_tag(pub C2Rust_alloc_tag_Inner);
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_alloc_tag_Inner {
    pub ct: codetag,
    pub counters: *mut alloc_tag_counters,
}
#[allow(dead_code, non_upper_case_globals)]
const C2Rust_alloc_tag_PADDING: usize =
    ::core::mem::size_of::<alloc_tag>() - ::core::mem::size_of::<C2Rust_alloc_tag_Inner>();
#[derive(Copy, Clone)]
#[repr(C)]
pub struct alloc_tag_counters {
    pub bytes: u64_0,
    pub calls: u64_0,
}
pub type u64_0 = __u64;
pub type __u64 = ::core::ffi::c_ulonglong;
#[derive(Copy, Clone)]
#[repr(C, align(8))]
pub struct codetag(pub C2Rust_codetag_Inner);
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_codetag_Inner {
    pub flags: ::core::ffi::c_uint,
    pub lineno: ::core::ffi::c_uint,
    pub modname: *const ::core::ffi::c_char,
    pub function: *const ::core::ffi::c_char,
    pub filename: *const ::core::ffi::c_char,
}
#[allow(dead_code, non_upper_case_globals)]
const C2Rust_codetag_PADDING: usize =
    ::core::mem::size_of::<codetag>() - ::core::mem::size_of::<C2Rust_codetag_Inner>();
pub type bool_0 = bool;
pub const r#false: C2Rust_Unnamed = 0;
pub type size_t = __kernel_size_t;
pub type __kernel_size_t = __kernel_ulong_t;
pub type __kernel_ulong_t = ::core::ffi::c_ulong;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct kmalloc_token_t {}
pub type gfp_t = ::core::ffi::c_uint;
pub const ___GFP_FS_BIT: C2Rust_Unnamed_73 = 7;
pub const ___GFP_IO_BIT: C2Rust_Unnamed_73 = 6;
pub const ___GFP_KSWAPD_RECLAIM_BIT: C2Rust_Unnamed_73 = 11;
pub const ___GFP_DIRECT_RECLAIM_BIT: C2Rust_Unnamed_73 = 10;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct kmem_cache {
    _private: [u8; 0],
}
pub type kmem_buckets = [*mut kmem_cache; 14];
pub type kmalloc_cache_type = ::core::ffi::c_uint;
pub const NR_KMALLOC_TYPES: kmalloc_cache_type = 1;
pub const KMALLOC_RECLAIM: kmalloc_cache_type = 0;
pub const KMALLOC_PARTITION_END: kmalloc_cache_type = 0;
pub const KMALLOC_PARTITION_START: kmalloc_cache_type = 0;
pub const KMALLOC_CGROUP: kmalloc_cache_type = 0;
pub const KMALLOC_DMA: kmalloc_cache_type = 0;
pub const KMALLOC_NORMAL: kmalloc_cache_type = 0;
pub const ___GFP_RECLAIMABLE_BIT: C2Rust_Unnamed_73 = 4;
pub const ___GFP_DMA_BIT: C2Rust_Unnamed_73 = 0;
pub const ___GFP_ACCOUNT_BIT: C2Rust_Unnamed_73 = 22;
pub const r#true: C2Rust_Unnamed = 1;
pub type __u8 = ::core::ffi::c_uchar;
pub type __u16 = ::core::ffi::c_ushort;
pub type __s32 = ::core::ffi::c_int;
pub type __u32 = ::core::ffi::c_uint;
pub type __s64 = ::core::ffi::c_longlong;
pub type u8_0 = __u8;
pub type u16_0 = __u16;
pub type s32 = __s32;
pub type u32_0 = __u32;
pub type s64 = __s64;
pub type C2Rust_Unnamed = ::core::ffi::c_uint;
pub type __kernel_long_t = ::core::ffi::c_long;
pub type __kernel_pid_t = ::core::ffi::c_int;
pub type __kernel_uid32_t = ::core::ffi::c_uint;
pub type __kernel_gid32_t = ::core::ffi::c_uint;
pub type __kernel_loff_t = ::core::ffi::c_longlong;
pub type __kernel_time64_t = ::core::ffi::c_longlong;
pub type __kernel_clock_t = __kernel_long_t;
pub type __kernel_timer_t = ::core::ffi::c_int;
pub type __kernel_clockid_t = ::core::ffi::c_int;
pub type __poll_t = ::core::ffi::c_uint;
pub type __kernel_dev_t = u32_0;
pub type dev_t = __kernel_dev_t;
pub type umode_t = ::core::ffi::c_ushort;
pub type pid_t = __kernel_pid_t;
pub type clockid_t = __kernel_clockid_t;
pub type uid_t = __kernel_uid32_t;
pub type gid_t = __kernel_gid32_t;
pub type loff_t = __kernel_loff_t;
pub type ssize_t = isize;
pub type uint32_t = u32;
pub type ktime_t = s64;
pub type sector_t = u64_0;
pub type blkcnt_t = u64_0;
pub type fmode_t = ::core::ffi::c_uint;
pub type phys_addr_t = u64_0;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct atomic_t {
    pub counter: ::core::ffi::c_int,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct atomic64_t {
    pub counter: s64,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct list_head {
    pub next: *mut list_head,
    pub prev: *mut list_head,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct hlist_head {
    pub first: *mut hlist_node,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct hlist_node {
    pub next: *mut hlist_node,
    pub pprev: *mut *mut hlist_node,
}
#[derive(Copy, Clone)]
#[repr(C, align(8))]
pub struct callback_head(pub C2Rust_callback_head_Inner);
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_callback_head_Inner {
    pub next: *mut callback_head,
    pub func: Option<unsafe extern "C" fn(*mut callback_head) -> ()>,
}
#[allow(dead_code, non_upper_case_globals)]
const C2Rust_callback_head_PADDING: usize =
    ::core::mem::size_of::<callback_head>() - ::core::mem::size_of::<C2Rust_callback_head_Inner>();
#[derive(Copy, Clone)]
#[repr(C)]
pub struct rcuwait {
    pub task: *mut task_struct,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct thread_struct {
    pub ra: ::core::ffi::c_ulong,
    pub sp: ::core::ffi::c_ulong,
    pub s: [::core::ffi::c_ulong; 12],
    pub fstate: __riscv_d_ext_state,
    pub bad_cause: ::core::ffi::c_ulong,
    pub envcfg: ::core::ffi::c_ulong,
    pub sum: ::core::ffi::c_ulong,
    pub riscv_v_flags: u32_0,
    pub vstate_ctrl: u32_0,
    pub vstate: __riscv_v_ext_state,
    pub align_ctl: ::core::ffi::c_ulong,
    pub kernel_vstate: __riscv_v_ext_state,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct __riscv_v_ext_state {
    pub vstart: ::core::ffi::c_ulong,
    pub vl: ::core::ffi::c_ulong,
    pub vtype: ::core::ffi::c_ulong,
    pub vcsr: ::core::ffi::c_ulong,
    pub vlenb: ::core::ffi::c_ulong,
    pub datap: *mut ::core::ffi::c_void,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct __riscv_d_ext_state {
    pub f: [__u64; 32],
    pub fcsr: __u32,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct bpf_net_context {
    _private: [u8; 0],
}
pub type refcount_t = refcount_struct;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct refcount_struct {
    pub refs: atomic_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct timer_list {
    pub entry: hlist_node,
    pub expires: ::core::ffi::c_ulong,
    pub function: Option<unsafe extern "C" fn(*mut timer_list) -> ()>,
    pub flags: u32_0,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct kmap_ctrl {}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct kunit {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct page_frag {
    pub page: *mut page,
    pub offset: __u32,
    pub size: __u32,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct page {
    pub flags: memdesc_flags_t,
    pub c2rust_unnamed: C2Rust_Unnamed_1,
    pub c2rust_unnamed_0: C2Rust_Unnamed_0,
    pub _refcount: atomic_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_0 {
    pub page_type: ::core::ffi::c_uint,
    pub _mapcount: atomic_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_1 {
    pub c2rust_unnamed: C2Rust_Unnamed_5,
    pub c2rust_unnamed_0: C2Rust_Unnamed_4,
    pub c2rust_unnamed_1: C2Rust_Unnamed_3,
    pub c2rust_unnamed_2: C2Rust_Unnamed_2,
    pub callback_head: callback_head,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_2 {
    pub _unused_pgmap_compound_info: *mut ::core::ffi::c_void,
    pub zone_device_data: *mut ::core::ffi::c_void,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_3 {
    pub compound_info: ::core::ffi::c_ulong,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_4 {
    pub pp_magic: ::core::ffi::c_ulong,
    pub pp: *mut page_pool,
    pub _pp_mapping_pad: ::core::ffi::c_ulong,
    pub dma_addr: ::core::ffi::c_ulong,
    pub pp_ref_count: atomic_long_t,
}
pub type atomic_long_t = atomic64_t;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct page_pool {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_5 {
    pub c2rust_unnamed: C2Rust_Unnamed_54,
    pub mapping: *mut address_space,
    pub c2rust_unnamed_0: C2Rust_Unnamed_6,
    pub private: ::core::ffi::c_ulong,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_6 {
    pub __folio_index: ::core::ffi::c_ulong,
    pub share: ::core::ffi::c_ulong,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct address_space {
    pub host: *mut inode,
    pub i_pages: xarray,
    pub invalidate_lock: rw_semaphore,
    pub gfp_mask: gfp_t,
    pub i_mmap_writable: atomic_t,
    pub i_mmap: rb_root_cached,
    pub nrpages: ::core::ffi::c_ulong,
    pub writeback_index: ::core::ffi::c_ulong,
    pub a_ops: *const address_space_operations,
    pub flags: ::core::ffi::c_ulong,
    pub wb_err: errseq_t,
    pub i_private_lock: spinlock_t,
    pub i_mmap_rwsem: rw_semaphore,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct rw_semaphore {
    pub count: atomic_long_t,
    pub owner: atomic_long_t,
    pub wait_lock: raw_spinlock_t,
    pub first_waiter: *mut rwsem_waiter,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct rwsem_waiter {
    _private: [u8; 0],
}
pub type raw_spinlock_t = raw_spinlock;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct raw_spinlock {
    pub raw_lock: arch_spinlock_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct arch_spinlock_t {}
pub type spinlock_t = spinlock;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct spinlock {
    pub c2rust_unnamed: C2Rust_Unnamed_7,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_7 {
    pub rlock: raw_spinlock,
}
pub type errseq_t = u32_0;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct address_space_operations {
    pub read_folio: Option<unsafe extern "C" fn(*mut file, *mut folio) -> ::core::ffi::c_int>,
    pub writepages: Option<
        unsafe extern "C" fn(*mut address_space, *mut writeback_control) -> ::core::ffi::c_int,
    >,
    pub dirty_folio: Option<unsafe extern "C" fn(*mut address_space, *mut folio) -> bool_0>,
    pub readahead: Option<unsafe extern "C" fn(*mut readahead_control) -> ()>,
    pub write_begin: Option<
        unsafe extern "C" fn(
            *const kiocb,
            *mut address_space,
            loff_t,
            ::core::ffi::c_uint,
            *mut *mut folio,
            *mut *mut ::core::ffi::c_void,
        ) -> ::core::ffi::c_int,
    >,
    pub write_end: Option<
        unsafe extern "C" fn(
            *const kiocb,
            *mut address_space,
            loff_t,
            ::core::ffi::c_uint,
            ::core::ffi::c_uint,
            *mut folio,
            *mut ::core::ffi::c_void,
        ) -> ::core::ffi::c_int,
    >,
    pub bmap: Option<unsafe extern "C" fn(*mut address_space, sector_t) -> sector_t>,
    pub invalidate_folio: Option<unsafe extern "C" fn(*mut folio, size_t, size_t) -> ()>,
    pub release_folio: Option<unsafe extern "C" fn(*mut folio, gfp_t) -> bool_0>,
    pub free_folio: Option<unsafe extern "C" fn(*mut folio) -> ()>,
    pub direct_IO: Option<unsafe extern "C" fn(*mut kiocb, *mut iov_iter) -> ssize_t>,
    pub migrate_folio: Option<
        unsafe extern "C" fn(
            *mut address_space,
            *mut folio,
            *mut folio,
            migrate_mode,
        ) -> ::core::ffi::c_int,
    >,
    pub launder_folio: Option<unsafe extern "C" fn(*mut folio) -> ::core::ffi::c_int>,
    pub is_partially_uptodate: Option<unsafe extern "C" fn(*mut folio, size_t, size_t) -> bool_0>,
    pub is_dirty_writeback:
        Option<unsafe extern "C" fn(*mut folio, *mut bool_0, *mut bool_0) -> ()>,
    pub error_remove_folio:
        Option<unsafe extern "C" fn(*mut address_space, *mut folio) -> ::core::ffi::c_int>,
    pub swap_activate: Option<
        unsafe extern "C" fn(*mut swap_info_struct, *mut file, *mut sector_t) -> ::core::ffi::c_int,
    >,
    pub swap_deactivate: Option<unsafe extern "C" fn(*mut file) -> ()>,
    pub swap_rw: Option<unsafe extern "C" fn(*mut kiocb, *mut iov_iter) -> ::core::ffi::c_int>,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct iov_iter {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct kiocb {
    pub ki_filp: *mut file,
    pub ki_pos: loff_t,
    pub ki_complete: Option<unsafe extern "C" fn(*mut kiocb, ::core::ffi::c_long) -> ()>,
    pub private: *mut ::core::ffi::c_void,
    pub ki_flags: ::core::ffi::c_int,
    pub ki_ioprio: u16_0,
    pub ki_write_stream: u8_0,
    pub ki_waitq: *mut wait_page_queue,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct wait_page_queue {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct file {
    pub f_lock: spinlock_t,
    pub f_mode: fmode_t,
    pub f_op: *const file_operations,
    pub f_mapping: *mut address_space,
    pub private_data: *mut ::core::ffi::c_void,
    pub f_inode: *mut inode,
    pub f_flags: ::core::ffi::c_uint,
    pub f_iocb_flags: ::core::ffi::c_uint,
    pub f_cred: *const cred,
    pub f_owner: *mut fown_struct,
    pub c2rust_unnamed: C2Rust_Unnamed_10,
    pub c2rust_unnamed_0: C2Rust_Unnamed_9,
    pub f_pos: loff_t,
    pub f_wb_err: errseq_t,
    pub f_sb_err: errseq_t,
    pub c2rust_unnamed_1: C2Rust_Unnamed_8,
    pub f_ref: file_ref_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct file_ref_t {
    pub refcnt: atomic64_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_8 {
    pub f_task_work: callback_head,
    pub f_llist: llist_node,
    pub f_ra: file_ra_state,
    pub f_freeptr: freeptr_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct freeptr_t {
    pub v: ::core::ffi::c_ulong,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct file_ra_state {
    pub start: ::core::ffi::c_ulong,
    pub size: ::core::ffi::c_uint,
    pub async_size: ::core::ffi::c_uint,
    pub ra_pages: ::core::ffi::c_uint,
    pub order: ::core::ffi::c_ushort,
    pub mmap_miss: ::core::ffi::c_ushort,
    pub prev_pos: loff_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct llist_node {
    pub next: *mut llist_node,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_9 {
    pub f_pos_lock: mutex,
    pub f_pipe: u64_0,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct mutex {
    pub owner: atomic_long_t,
    pub wait_lock: raw_spinlock_t,
    pub first_waiter: *mut mutex_waiter,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct mutex_waiter {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_10 {
    pub f_path: path,
    pub __f_path: path,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct path {
    pub mnt: *mut vfsmount,
    pub dentry: *mut dentry,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct dentry {
    pub d_flags: ::core::ffi::c_uint,
    pub d_seq: seqcount_spinlock_t,
    pub d_hash: hlist_bl_node,
    pub d_parent: *mut dentry,
    pub c2rust_unnamed: C2Rust_Unnamed_36,
    pub d_inode: *mut inode,
    pub d_shortname: shortname_store,
    pub d_op: *const dentry_operations,
    pub d_sb: *mut super_block,
    pub d_time: ::core::ffi::c_ulong,
    pub d_fsdata: *mut ::core::ffi::c_void,
    pub d_lockref: lockref,
    pub d_lru: list_head,
    pub d_sib: hlist_node,
    pub d_children: hlist_head,
    pub c2rust_unnamed_0: C2Rust_Unnamed_11,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_11 {
    pub d_alias: hlist_node,
    pub d_in_lookup_hash: hlist_bl_node,
    pub d_rcu: callback_head,
    pub waiters: *mut completion_list,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct completion_list {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct hlist_bl_node {
    pub next: *mut hlist_bl_node,
    pub pprev: *mut *mut hlist_bl_node,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct lockref {
    pub c2rust_unnamed: C2Rust_Unnamed_12,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_12 {
    pub c2rust_unnamed: C2Rust_Unnamed_13,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_13 {
    pub lock: spinlock_t,
    pub count: ::core::ffi::c_int,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct super_block {
    pub s_list: list_head,
    pub s_dev: dev_t,
    pub s_blocksize_bits: ::core::ffi::c_uchar,
    pub s_blocksize: ::core::ffi::c_ulong,
    pub s_maxbytes: loff_t,
    pub s_type: *mut file_system_type,
    pub s_op: *const super_operations,
    pub dq_op: *const dquot_operations,
    pub s_qcop: *const quotactl_ops,
    pub s_export_op: *const export_operations,
    pub s_flags: ::core::ffi::c_ulong,
    pub s_iflags: ::core::ffi::c_ulong,
    pub s_magic: ::core::ffi::c_ulong,
    pub s_root: *mut dentry,
    pub s_umount: rw_semaphore,
    pub s_count: ::core::ffi::c_int,
    pub s_active: atomic_t,
    pub s_xattr: *const *const xattr_handler,
    pub s_roots: hlist_head,
    pub s_roots_lock: spinlock_t,
    pub s_mounts: *mut mount,
    pub s_bdev: *mut block_device,
    pub s_bdev_file: *mut file,
    pub s_bdi: *mut backing_dev_info,
    pub s_mtd: *mut mtd_info,
    pub s_instances: hlist_node,
    pub s_quota_types: ::core::ffi::c_uint,
    pub s_dquot: quota_info,
    pub s_writers: sb_writers,
    pub s_fs_info: *mut ::core::ffi::c_void,
    pub s_time_gran: u32_0,
    pub s_time_min: time64_t,
    pub s_time_max: time64_t,
    pub s_id: [::core::ffi::c_char; 32],
    pub s_uuid: uuid_t,
    pub s_uuid_len: u8_0,
    pub s_sysfs_name: [::core::ffi::c_char; 37],
    pub s_max_links: ::core::ffi::c_uint,
    pub s_d_flags: ::core::ffi::c_uint,
    pub s_vfs_rename_mutex: mutex,
    pub s_subtype: *const ::core::ffi::c_char,
    pub __s_d_op: *const dentry_operations,
    pub s_shrink: *mut shrinker,
    pub s_remove_count: atomic_long_t,
    pub s_readonly_remount: ::core::ffi::c_int,
    pub s_wb_err: errseq_t,
    pub s_dio_done_wq: *mut workqueue_struct,
    pub s_pins: hlist_head,
    pub s_user_ns: *mut user_namespace,
    pub s_dentry_lru: list_lru,
    pub s_inode_lru: list_lru,
    pub rcu: callback_head,
    pub destroy_work: work_struct,
    pub s_sync_lock: mutex,
    pub s_stack_depth: ::core::ffi::c_int,
    pub s_inode_list_lock: spinlock_t,
    pub s_inodes: list_head,
    pub s_inode_wblist_lock: spinlock_t,
    pub s_inodes_wb: list_head,
    pub s_min_writeback_pages: ::core::ffi::c_long,
    pub s_pending_errors: refcount_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct work_struct {
    pub data: atomic_long_t,
    pub entry: list_head,
    pub func: work_func_t,
}
pub type work_func_t = Option<unsafe extern "C" fn(*mut work_struct) -> ()>;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct list_lru {
    pub node: *mut list_lru_node,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct list_lru_node {
    pub lru: list_lru_one,
    pub nr_items: atomic_long_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct list_lru_one {
    pub list: list_head,
    pub nr_items: ::core::ffi::c_long,
    pub lock: spinlock_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct user_namespace {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct workqueue_struct {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct shrinker {
    pub count_objects:
        Option<unsafe extern "C" fn(*mut shrinker, *mut shrink_control) -> ::core::ffi::c_ulong>,
    pub scan_objects:
        Option<unsafe extern "C" fn(*mut shrinker, *mut shrink_control) -> ::core::ffi::c_ulong>,
    pub batch: ::core::ffi::c_long,
    pub seeks: ::core::ffi::c_int,
    pub flags: ::core::ffi::c_uint,
    pub refcount: refcount_t,
    pub done: completion,
    pub rcu: callback_head,
    pub private_data: *mut ::core::ffi::c_void,
    pub list: list_head,
    pub nr_deferred: *mut atomic_long_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct completion {
    pub done: ::core::ffi::c_uint,
    pub wait: swait_queue_head,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct swait_queue_head {
    pub lock: raw_spinlock_t,
    pub task_list: list_head,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct shrink_control {
    pub gfp_mask: gfp_t,
    pub nid: ::core::ffi::c_int,
    pub nr_to_scan: ::core::ffi::c_ulong,
    pub nr_scanned: ::core::ffi::c_ulong,
    pub memcg: *mut mem_cgroup,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct mem_cgroup {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct dentry_operations {
    pub d_revalidate: Option<
        unsafe extern "C" fn(
            *mut inode,
            *const qstr,
            *mut dentry,
            ::core::ffi::c_uint,
        ) -> ::core::ffi::c_int,
    >,
    pub d_weak_revalidate:
        Option<unsafe extern "C" fn(*mut dentry, ::core::ffi::c_uint) -> ::core::ffi::c_int>,
    pub d_hash: Option<unsafe extern "C" fn(*const dentry, *mut qstr) -> ::core::ffi::c_int>,
    pub d_compare: Option<
        unsafe extern "C" fn(
            *const dentry,
            ::core::ffi::c_uint,
            *const ::core::ffi::c_char,
            *const qstr,
        ) -> ::core::ffi::c_int,
    >,
    pub d_delete: Option<unsafe extern "C" fn(*const dentry) -> ::core::ffi::c_int>,
    pub d_init: Option<unsafe extern "C" fn(*mut dentry) -> ::core::ffi::c_int>,
    pub d_release: Option<unsafe extern "C" fn(*mut dentry) -> ()>,
    pub d_prune: Option<unsafe extern "C" fn(*mut dentry) -> ()>,
    pub d_iput: Option<unsafe extern "C" fn(*mut dentry, *mut inode) -> ()>,
    pub d_dname: Option<
        unsafe extern "C" fn(
            *mut dentry,
            *mut ::core::ffi::c_char,
            ::core::ffi::c_int,
        ) -> *mut ::core::ffi::c_char,
    >,
    pub d_automount: Option<unsafe extern "C" fn(*mut path) -> *mut vfsmount>,
    pub d_manage: Option<unsafe extern "C" fn(*const path, bool_0) -> ::core::ffi::c_int>,
    pub d_real: Option<unsafe extern "C" fn(*mut dentry, d_real_type) -> *mut dentry>,
    pub d_unalias_trylock: Option<unsafe extern "C" fn(*const dentry) -> bool_0>,
    pub d_unalias_unlock: Option<unsafe extern "C" fn(*const dentry) -> ()>,
}
pub type d_real_type = ::core::ffi::c_uint;
pub const D_REAL_METADATA: d_real_type = 1;
pub const D_REAL_DATA: d_real_type = 0;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct vfsmount {
    pub mnt_root: *mut dentry,
    pub mnt_sb: *mut super_block,
    pub mnt_flags: ::core::ffi::c_int,
    pub mnt_idmap: *mut mnt_idmap,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct mnt_idmap {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct inode {
    pub i_mode: umode_t,
    pub i_opflags: ::core::ffi::c_ushort,
    pub i_flags: ::core::ffi::c_uint,
    pub i_uid: kuid_t,
    pub i_gid: kgid_t,
    pub i_op: *const inode_operations,
    pub i_sb: *mut super_block,
    pub i_mapping: *mut address_space,
    pub i_ino: u64_0,
    pub c2rust_unnamed: C2Rust_Unnamed_30,
    pub i_rdev: dev_t,
    pub i_size: loff_t,
    pub i_atime_sec: time64_t,
    pub i_mtime_sec: time64_t,
    pub i_ctime_sec: time64_t,
    pub i_atime_nsec: u32_0,
    pub i_mtime_nsec: u32_0,
    pub i_ctime_nsec: u32_0,
    pub i_generation: u32_0,
    pub i_lock: spinlock_t,
    pub i_bytes: ::core::ffi::c_ushort,
    pub i_blkbits: u8_0,
    pub i_write_hint: rw_hint,
    pub i_blocks: blkcnt_t,
    pub i_state: inode_state_flags,
    pub i_rwsem: rw_semaphore,
    pub dirtied_when: ::core::ffi::c_ulong,
    pub dirtied_time_when: ::core::ffi::c_ulong,
    pub i_hash: hlist_node,
    pub i_io_list: list_head,
    pub i_lru: list_head,
    pub i_sb_list: list_head,
    pub i_wb_list: list_head,
    pub c2rust_unnamed_0: C2Rust_Unnamed_29,
    pub i_version: atomic64_t,
    pub i_sequence: atomic64_t,
    pub i_count: atomic_t,
    pub i_dio_count: atomic_t,
    pub i_writecount: atomic_t,
    pub c2rust_unnamed_1: C2Rust_Unnamed_16,
    pub i_flctx: *mut file_lock_context,
    pub i_data: address_space,
    pub c2rust_unnamed_2: C2Rust_Unnamed_15,
    pub c2rust_unnamed_3: C2Rust_Unnamed_14,
    pub i_private: *mut ::core::ffi::c_void,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_14 {
    pub i_pipe: *mut pipe_inode_info,
    pub i_cdev: *mut cdev,
    pub i_link: *mut ::core::ffi::c_char,
    pub i_dir_seq: ::core::ffi::c_uint,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct cdev {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct pipe_inode_info {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_15 {
    pub i_devices: list_head,
    pub i_linklen: ::core::ffi::c_int,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct file_lock_context {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_16 {
    pub i_fop: *const file_operations,
    pub free_inode: Option<unsafe extern "C" fn(*mut inode) -> ()>,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct file_operations {
    pub owner: *mut module,
    pub fop_flags: fop_flags_t,
    pub llseek: Option<unsafe extern "C" fn(*mut file, loff_t, ::core::ffi::c_int) -> loff_t>,
    pub read: Option<
        unsafe extern "C" fn(*mut file, *mut ::core::ffi::c_char, size_t, *mut loff_t) -> ssize_t,
    >,
    pub write: Option<
        unsafe extern "C" fn(*mut file, *const ::core::ffi::c_char, size_t, *mut loff_t) -> ssize_t,
    >,
    pub read_iter: Option<unsafe extern "C" fn(*mut kiocb, *mut iov_iter) -> ssize_t>,
    pub write_iter: Option<unsafe extern "C" fn(*mut kiocb, *mut iov_iter) -> ssize_t>,
    pub iopoll: Option<
        unsafe extern "C" fn(
            *mut kiocb,
            *mut io_comp_batch,
            ::core::ffi::c_uint,
        ) -> ::core::ffi::c_int,
    >,
    pub iterate_shared:
        Option<unsafe extern "C" fn(*mut file, *mut dir_context) -> ::core::ffi::c_int>,
    pub poll: Option<unsafe extern "C" fn(*mut file, *mut poll_table_struct) -> __poll_t>,
    pub unlocked_ioctl: Option<
        unsafe extern "C" fn(
            *mut file,
            ::core::ffi::c_uint,
            ::core::ffi::c_ulong,
        ) -> ::core::ffi::c_long,
    >,
    pub compat_ioctl: Option<
        unsafe extern "C" fn(
            *mut file,
            ::core::ffi::c_uint,
            ::core::ffi::c_ulong,
        ) -> ::core::ffi::c_long,
    >,
    pub mmap: Option<unsafe extern "C" fn(*mut file, *mut vm_area_struct) -> ::core::ffi::c_int>,
    pub open: Option<unsafe extern "C" fn(*mut inode, *mut file) -> ::core::ffi::c_int>,
    pub flush: Option<unsafe extern "C" fn(*mut file, fl_owner_t) -> ::core::ffi::c_int>,
    pub release: Option<unsafe extern "C" fn(*mut inode, *mut file) -> ::core::ffi::c_int>,
    pub fsync: Option<
        unsafe extern "C" fn(*mut file, loff_t, loff_t, ::core::ffi::c_int) -> ::core::ffi::c_int,
    >,
    pub fasync: Option<
        unsafe extern "C" fn(
            ::core::ffi::c_int,
            *mut file,
            ::core::ffi::c_int,
        ) -> ::core::ffi::c_int,
    >,
    pub lock: Option<
        unsafe extern "C" fn(*mut file, ::core::ffi::c_int, *mut file_lock) -> ::core::ffi::c_int,
    >,
    pub get_unmapped_area: Option<
        unsafe extern "C" fn(
            *mut file,
            ::core::ffi::c_ulong,
            ::core::ffi::c_ulong,
            ::core::ffi::c_ulong,
            ::core::ffi::c_ulong,
        ) -> ::core::ffi::c_ulong,
    >,
    pub check_flags: Option<unsafe extern "C" fn(::core::ffi::c_int) -> ::core::ffi::c_int>,
    pub flock: Option<
        unsafe extern "C" fn(*mut file, ::core::ffi::c_int, *mut file_lock) -> ::core::ffi::c_int,
    >,
    pub splice_write: Option<
        unsafe extern "C" fn(
            *mut pipe_inode_info,
            *mut file,
            *mut loff_t,
            size_t,
            ::core::ffi::c_uint,
        ) -> ssize_t,
    >,
    pub splice_read: Option<
        unsafe extern "C" fn(
            *mut file,
            *mut loff_t,
            *mut pipe_inode_info,
            size_t,
            ::core::ffi::c_uint,
        ) -> ssize_t,
    >,
    pub splice_eof: Option<unsafe extern "C" fn(*mut file) -> ()>,
    pub setlease: Option<
        unsafe extern "C" fn(
            *mut file,
            ::core::ffi::c_int,
            *mut *mut file_lease,
            *mut *mut ::core::ffi::c_void,
        ) -> ::core::ffi::c_int,
    >,
    pub fallocate: Option<
        unsafe extern "C" fn(*mut file, ::core::ffi::c_int, loff_t, loff_t) -> ::core::ffi::c_long,
    >,
    pub show_fdinfo: Option<unsafe extern "C" fn(*mut seq_file, *mut file) -> ()>,
    pub copy_file_range: Option<
        unsafe extern "C" fn(
            *mut file,
            loff_t,
            *mut file,
            loff_t,
            size_t,
            ::core::ffi::c_uint,
        ) -> ssize_t,
    >,
    pub remap_file_range: Option<
        unsafe extern "C" fn(
            *mut file,
            loff_t,
            *mut file,
            loff_t,
            loff_t,
            ::core::ffi::c_uint,
        ) -> loff_t,
    >,
    pub fadvise: Option<
        unsafe extern "C" fn(*mut file, loff_t, loff_t, ::core::ffi::c_int) -> ::core::ffi::c_int,
    >,
    pub uring_cmd:
        Option<unsafe extern "C" fn(*mut io_uring_cmd, ::core::ffi::c_uint) -> ::core::ffi::c_int>,
    pub uring_cmd_iopoll: Option<
        unsafe extern "C" fn(
            *mut io_uring_cmd,
            *mut io_comp_batch,
            ::core::ffi::c_uint,
        ) -> ::core::ffi::c_int,
    >,
    pub mmap_prepare: Option<unsafe extern "C" fn(*mut vm_area_desc) -> ::core::ffi::c_int>,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct vm_area_desc {
    pub mm: *mut mm_struct,
    pub file: *mut file,
    pub start: ::core::ffi::c_ulong,
    pub end: ::core::ffi::c_ulong,
    pub pgoff: ::core::ffi::c_ulong,
    pub vm_file: *mut file,
    pub vma_flags: vma_flags_t,
    pub page_prot: pgprot_t,
    pub vm_ops: *const vm_operations_struct,
    pub private_data: *mut ::core::ffi::c_void,
    pub action: mmap_action,
}
pub type mmap_action_type = ::core::ffi::c_uint;
pub const MMAP_MAP_KERNEL_PAGES: mmap_action_type = 4;
pub const MMAP_SIMPLE_IO_REMAP: mmap_action_type = 3;
pub const MMAP_IO_REMAP_PFN: mmap_action_type = 2;
pub const MMAP_REMAP_PFN: mmap_action_type = 1;
pub const MMAP_NOTHING: mmap_action_type = 0;
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_17 {
    pub remap: C2Rust_Unnamed_20,
    pub simple_ioremap: C2Rust_Unnamed_19,
    pub map_kernel: C2Rust_Unnamed_18,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_18 {
    pub start: ::core::ffi::c_ulong,
    pub pages: *mut *mut page,
    pub nr_pages: ::core::ffi::c_ulong,
    pub pgoff: ::core::ffi::c_ulong,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_19 {
    pub start_phys_addr: phys_addr_t,
    pub size: ::core::ffi::c_ulong,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_20 {
    pub start: ::core::ffi::c_ulong,
    pub start_pfn: ::core::ffi::c_ulong,
    pub size: ::core::ffi::c_ulong,
    pub pgprot: pgprot_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct pgprot_t {
    pub pgprot: ::core::ffi::c_ulong,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct vm_operations_struct {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct vma_flags_t {
    pub __vma_flags: [::core::ffi::c_ulong; 1],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct mm_struct {
    pub c2rust_unnamed: C2Rust_Unnamed_21,
    pub flexible_array: [::core::ffi::c_char; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_21 {
    pub c2rust_unnamed: C2Rust_Unnamed_24,
    pub mm_mt: maple_tree,
    pub mmap_base: ::core::ffi::c_ulong,
    pub mmap_legacy_base: ::core::ffi::c_ulong,
    pub task_size: ::core::ffi::c_ulong,
    pub pgd: *mut pgd_t,
    pub mm_users: atomic_t,
    pub mm_cid: mm_mm_cid,
    pub sc_stat: sched_cache_stat,
    pub pgtables_bytes: atomic_long_t,
    pub map_count: ::core::ffi::c_int,
    pub page_table_lock: spinlock_t,
    pub mmap_lock: rw_semaphore,
    pub mmlist: list_head,
    pub futex: futex_mm_data,
    pub hiwater_rss: ::core::ffi::c_ulong,
    pub hiwater_vm: ::core::ffi::c_ulong,
    pub total_vm: ::core::ffi::c_ulong,
    pub locked_vm: ::core::ffi::c_ulong,
    pub pinned_vm: atomic64_t,
    pub data_vm: ::core::ffi::c_ulong,
    pub exec_vm: ::core::ffi::c_ulong,
    pub stack_vm: ::core::ffi::c_ulong,
    pub c2rust_unnamed_0: C2Rust_Unnamed_22,
    pub write_protect_seq: seqcount_t,
    pub arg_lock: spinlock_t,
    pub start_code: ::core::ffi::c_ulong,
    pub end_code: ::core::ffi::c_ulong,
    pub start_data: ::core::ffi::c_ulong,
    pub end_data: ::core::ffi::c_ulong,
    pub start_brk: ::core::ffi::c_ulong,
    pub brk: ::core::ffi::c_ulong,
    pub start_stack: ::core::ffi::c_ulong,
    pub arg_start: ::core::ffi::c_ulong,
    pub arg_end: ::core::ffi::c_ulong,
    pub env_start: ::core::ffi::c_ulong,
    pub env_end: ::core::ffi::c_ulong,
    pub saved_auxv: [::core::ffi::c_ulong; 70],
    pub rss_stat: [percpu_counter; 4],
    pub binfmt: *mut linux_binfmt,
    pub context: mm_context_t,
    pub flags: mm_flags_t,
    pub exe_file: *mut file,
    pub tlb_flush_pending: atomic_t,
    pub tlb_flush_batched: atomic_t,
    pub uprobes_state: uprobes_state,
    pub async_put_work: work_struct,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct uprobes_state {}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct mm_flags_t {
    pub __mm_flags: [::core::ffi::c_ulong; 1],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct mm_context_t {
    pub id: atomic_long_t,
    pub vdso: *mut ::core::ffi::c_void,
    pub flags: ::core::ffi::c_ulong,
    pub pmlen: u8_0,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct linux_binfmt {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct percpu_counter {
    pub count: s64,
}
pub type seqcount_t = seqcount;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct seqcount {
    pub sequence: ::core::ffi::c_uint,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_22 {
    pub def_flags: vm_flags_t,
    pub def_vma_flags: vma_flags_t,
}
pub type vm_flags_t = ::core::ffi::c_ulong;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct futex_mm_data {}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct sched_cache_stat {}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct mm_mm_cid {}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct pgd_t {
    pub pgd: ::core::ffi::c_ulong,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct maple_tree {
    pub c2rust_unnamed: C2Rust_Unnamed_23,
    pub ma_flags: ::core::ffi::c_uint,
    pub ma_root: *mut ::core::ffi::c_void,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_23 {
    pub ma_lock: spinlock_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_24 {
    pub mm_count: atomic_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct io_comp_batch {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct io_uring_cmd {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct seq_file {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct file_lease {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct file_lock {
    _private: [u8; 0],
}
pub type fl_owner_t = *mut ::core::ffi::c_void;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct vm_area_struct {
    pub c2rust_unnamed: C2Rust_Unnamed_27,
    pub vm_mm: *mut mm_struct,
    pub vm_page_prot: pgprot_t,
    pub c2rust_unnamed_0: C2Rust_Unnamed_26,
    pub anon_vma_chain: list_head,
    pub anon_vma: *mut anon_vma,
    pub vm_ops: *const vm_operations_struct,
    pub vm_pgoff: ::core::ffi::c_ulong,
    pub vm_file: *mut file,
    pub vm_private_data: *mut ::core::ffi::c_void,
    pub swap_readahead_info: atomic_long_t,
    pub shared: C2Rust_Unnamed_25,
    pub vm_userfaultfd_ctx: vm_userfaultfd_ctx,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct vm_userfaultfd_ctx {}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_25 {
    pub rb: rb_node,
    pub rb_subtree_last: ::core::ffi::c_ulong,
}
#[derive(Copy, Clone)]
#[repr(C, align(8))]
pub struct rb_node(pub C2Rust_rb_node_Inner);
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_rb_node_Inner {
    pub __rb_parent_color: ::core::ffi::c_ulong,
    pub rb_right: *mut rb_node,
    pub rb_left: *mut rb_node,
}
#[allow(dead_code, non_upper_case_globals)]
const C2Rust_rb_node_PADDING: usize =
    ::core::mem::size_of::<rb_node>() - ::core::mem::size_of::<C2Rust_rb_node_Inner>();
#[derive(Copy, Clone)]
#[repr(C)]
pub struct anon_vma {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_26 {
    pub vm_flags: vm_flags_t,
    pub flags: vma_flags_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_27 {
    pub c2rust_unnamed: C2Rust_Unnamed_28,
    pub vm_freeptr: freeptr_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_28 {
    pub vm_start: ::core::ffi::c_ulong,
    pub vm_end: ::core::ffi::c_ulong,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct poll_table_struct {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct dir_context {
    pub actor: filldir_t,
    pub pos: loff_t,
    pub count: ::core::ffi::c_int,
    pub dt_flags_mask: ::core::ffi::c_uint,
}
pub type filldir_t = Option<
    unsafe extern "C" fn(
        *mut dir_context,
        *const ::core::ffi::c_char,
        ::core::ffi::c_int,
        loff_t,
        u64_0,
        ::core::ffi::c_uint,
    ) -> bool_0,
>;
pub type fop_flags_t = ::core::ffi::c_uint;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct module {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_29 {
    pub i_dentry: hlist_head,
    pub i_rcu: callback_head,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct inode_state_flags {
    pub __state: inode_state_flags_enum,
}
pub type inode_state_flags_enum = ::core::ffi::c_uint;
pub const I_PINNING_NETFS_WB: inode_state_flags_enum = 262144;
pub const I_SYNC_QUEUED: inode_state_flags_enum = 131072;
pub const I_DONTCACHE: inode_state_flags_enum = 65536;
pub const I_CREATING: inode_state_flags_enum = 32768;
pub const I_OVL_INUSE: inode_state_flags_enum = 16384;
pub const I_WB_SWITCH: inode_state_flags_enum = 8192;
pub const I_DIRTY_TIME: inode_state_flags_enum = 4096;
pub const I_LINKABLE: inode_state_flags_enum = 2048;
pub const I_REFERENCED: inode_state_flags_enum = 1024;
pub const I_CLEAR: inode_state_flags_enum = 512;
pub const I_FREEING: inode_state_flags_enum = 256;
pub const I_WILL_FREE: inode_state_flags_enum = 128;
pub const I_DIRTY_PAGES: inode_state_flags_enum = 64;
pub const I_DIRTY_DATASYNC: inode_state_flags_enum = 32;
pub const I_DIRTY_SYNC: inode_state_flags_enum = 16;
pub const I_LRU_ISOLATING: inode_state_flags_enum = 4;
pub const I_SYNC: inode_state_flags_enum = 2;
pub const I_NEW: inode_state_flags_enum = 1;
pub type rw_hint = ::core::ffi::c_uchar;
pub const WRITE_LIFE_HINT_NR: rw_hint = 6;
pub const WRITE_LIFE_EXTREME: rw_hint = 5;
pub const WRITE_LIFE_LONG: rw_hint = 4;
pub const WRITE_LIFE_MEDIUM: rw_hint = 3;
pub const WRITE_LIFE_SHORT: rw_hint = 2;
pub const WRITE_LIFE_NONE: rw_hint = 1;
pub const WRITE_LIFE_NOT_SET: rw_hint = 0;
pub type time64_t = __s64;
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_30 {
    pub i_nlink: ::core::ffi::c_uint,
    pub __i_nlink: ::core::ffi::c_uint,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct inode_operations {
    pub lookup:
        Option<unsafe extern "C" fn(*mut inode, *mut dentry, ::core::ffi::c_uint) -> *mut dentry>,
    pub get_link: Option<
        unsafe extern "C" fn(
            *mut dentry,
            *mut inode,
            *mut delayed_call,
        ) -> *const ::core::ffi::c_char,
    >,
    pub permission: Option<
        unsafe extern "C" fn(*mut mnt_idmap, *mut inode, ::core::ffi::c_int) -> ::core::ffi::c_int,
    >,
    pub get_inode_acl:
        Option<unsafe extern "C" fn(*mut inode, ::core::ffi::c_int, bool_0) -> *mut posix_acl>,
    pub readlink: Option<
        unsafe extern "C" fn(
            *mut dentry,
            *mut ::core::ffi::c_char,
            ::core::ffi::c_int,
        ) -> ::core::ffi::c_int,
    >,
    pub create: Option<
        unsafe extern "C" fn(
            *mut mnt_idmap,
            *mut inode,
            *mut dentry,
            umode_t,
            bool_0,
        ) -> ::core::ffi::c_int,
    >,
    pub link:
        Option<unsafe extern "C" fn(*mut dentry, *mut inode, *mut dentry) -> ::core::ffi::c_int>,
    pub unlink: Option<unsafe extern "C" fn(*mut inode, *mut dentry) -> ::core::ffi::c_int>,
    pub symlink: Option<
        unsafe extern "C" fn(
            *mut mnt_idmap,
            *mut inode,
            *mut dentry,
            *const ::core::ffi::c_char,
        ) -> ::core::ffi::c_int,
    >,
    pub mkdir: Option<
        unsafe extern "C" fn(*mut mnt_idmap, *mut inode, *mut dentry, umode_t) -> *mut dentry,
    >,
    pub rmdir: Option<unsafe extern "C" fn(*mut inode, *mut dentry) -> ::core::ffi::c_int>,
    pub mknod: Option<
        unsafe extern "C" fn(
            *mut mnt_idmap,
            *mut inode,
            *mut dentry,
            umode_t,
            dev_t,
        ) -> ::core::ffi::c_int,
    >,
    pub rename: Option<
        unsafe extern "C" fn(
            *mut mnt_idmap,
            *mut inode,
            *mut dentry,
            *mut inode,
            *mut dentry,
            ::core::ffi::c_uint,
        ) -> ::core::ffi::c_int,
    >,
    pub setattr:
        Option<unsafe extern "C" fn(*mut mnt_idmap, *mut dentry, *mut iattr) -> ::core::ffi::c_int>,
    pub getattr: Option<
        unsafe extern "C" fn(
            *mut mnt_idmap,
            *const path,
            *mut kstat,
            u32_0,
            ::core::ffi::c_uint,
        ) -> ::core::ffi::c_int,
    >,
    pub listxattr:
        Option<unsafe extern "C" fn(*mut dentry, *mut ::core::ffi::c_char, size_t) -> ssize_t>,
    pub fiemap: Option<
        unsafe extern "C" fn(
            *mut inode,
            *mut fiemap_extent_info,
            u64_0,
            u64_0,
        ) -> ::core::ffi::c_int,
    >,
    pub update_time: Option<
        unsafe extern "C" fn(*mut inode, fs_update_time, ::core::ffi::c_uint) -> ::core::ffi::c_int,
    >,
    pub sync_lazytime: Option<unsafe extern "C" fn(*mut inode) -> ()>,
    pub atomic_open: Option<
        unsafe extern "C" fn(
            *mut inode,
            *mut dentry,
            *mut file,
            ::core::ffi::c_uint,
            umode_t,
        ) -> ::core::ffi::c_int,
    >,
    pub tmpfile: Option<
        unsafe extern "C" fn(*mut mnt_idmap, *mut inode, *mut file, umode_t) -> ::core::ffi::c_int,
    >,
    pub get_acl: Option<
        unsafe extern "C" fn(*mut mnt_idmap, *mut dentry, ::core::ffi::c_int) -> *mut posix_acl,
    >,
    pub set_acl: Option<
        unsafe extern "C" fn(
            *mut mnt_idmap,
            *mut dentry,
            *mut posix_acl,
            ::core::ffi::c_int,
        ) -> ::core::ffi::c_int,
    >,
    pub fileattr_set: Option<
        unsafe extern "C" fn(*mut mnt_idmap, *mut dentry, *mut file_kattr) -> ::core::ffi::c_int,
    >,
    pub fileattr_get:
        Option<unsafe extern "C" fn(*mut dentry, *mut file_kattr) -> ::core::ffi::c_int>,
    pub get_offset_ctx: Option<unsafe extern "C" fn(*mut inode) -> *mut offset_ctx>,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct offset_ctx {
    pub mt: maple_tree,
    pub next_offset: ::core::ffi::c_ulong,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct file_kattr {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct posix_acl {
    _private: [u8; 0],
}
pub type fs_update_time = ::core::ffi::c_uint;
pub const FS_UPD_CMTIME: fs_update_time = 1;
pub const FS_UPD_ATIME: fs_update_time = 0;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct fiemap_extent_info {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct kstat {
    pub result_mask: u32_0,
    pub mode: umode_t,
    pub nlink: ::core::ffi::c_uint,
    pub blksize: uint32_t,
    pub attributes: u64_0,
    pub attributes_mask: u64_0,
    pub ino: u64_0,
    pub dev: dev_t,
    pub rdev: dev_t,
    pub uid: kuid_t,
    pub gid: kgid_t,
    pub size: loff_t,
    pub atime: timespec64,
    pub mtime: timespec64,
    pub ctime: timespec64,
    pub btime: timespec64,
    pub blocks: u64_0,
    pub mnt_id: u64_0,
    pub change_cookie: u64_0,
    pub subvol: u64_0,
    pub dio_mem_align: u32_0,
    pub dio_offset_align: u32_0,
    pub dio_read_offset_align: u32_0,
    pub atomic_write_unit_min: u32_0,
    pub atomic_write_unit_max: u32_0,
    pub atomic_write_unit_max_opt: u32_0,
    pub atomic_write_segments_max: u32_0,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct timespec64 {
    pub tv_sec: time64_t,
    pub tv_nsec: ::core::ffi::c_long,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct kgid_t {
    pub val: gid_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct kuid_t {
    pub val: uid_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct iattr {
    pub ia_valid: ::core::ffi::c_uint,
    pub ia_mode: umode_t,
    pub c2rust_unnamed: C2Rust_Unnamed_32,
    pub c2rust_unnamed_0: C2Rust_Unnamed_31,
    pub ia_size: loff_t,
    pub ia_atime: timespec64,
    pub ia_mtime: timespec64,
    pub ia_ctime: timespec64,
    pub ia_file: *mut file,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_31 {
    pub ia_gid: kgid_t,
    pub ia_vfsgid: vfsgid_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct vfsgid_t {
    pub val: gid_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_32 {
    pub ia_uid: kuid_t,
    pub ia_vfsuid: vfsuid_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct vfsuid_t {
    pub val: uid_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct delayed_call {
    pub r#fn: Option<unsafe extern "C" fn(*mut ::core::ffi::c_void) -> ()>,
    pub arg: *mut ::core::ffi::c_void,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct qstr {
    pub c2rust_unnamed: C2Rust_Unnamed_33,
    pub name: *const ::core::ffi::c_uchar,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_33 {
    pub c2rust_unnamed: C2Rust_Unnamed_34,
    pub hash_len: u64_0,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_34 {
    pub hash: u32_0,
    pub len: u32_0,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct uuid_t {
    pub b: [__u8; 16],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct sb_writers {
    pub frozen: ::core::ffi::c_ushort,
    pub freeze_kcount: ::core::ffi::c_int,
    pub freeze_ucount: ::core::ffi::c_int,
    pub freeze_owner: *const ::core::ffi::c_void,
    pub rw_sem: [percpu_rw_semaphore; 3],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct percpu_rw_semaphore {
    pub rss: rcu_sync,
    pub read_count: *mut ::core::ffi::c_uint,
    pub writer: rcuwait,
    pub waiters: wait_queue_head_t,
    pub block: atomic_t,
}
pub type wait_queue_head_t = wait_queue_head;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct wait_queue_head {
    pub lock: spinlock_t,
    pub head: list_head,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct rcu_sync {
    pub gp_state: ::core::ffi::c_int,
    pub gp_count: ::core::ffi::c_int,
    pub gp_wait: wait_queue_head_t,
    pub cb_head: callback_head,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct quota_info {
    pub flags: ::core::ffi::c_uint,
    pub dqio_sem: rw_semaphore,
    pub files: [*mut inode; 3],
    pub info: [mem_dqinfo; 3],
    pub ops: [*const quota_format_ops; 3],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct quota_format_ops {
    pub check_quota_file:
        Option<unsafe extern "C" fn(*mut super_block, ::core::ffi::c_int) -> ::core::ffi::c_int>,
    pub read_file_info:
        Option<unsafe extern "C" fn(*mut super_block, ::core::ffi::c_int) -> ::core::ffi::c_int>,
    pub write_file_info:
        Option<unsafe extern "C" fn(*mut super_block, ::core::ffi::c_int) -> ::core::ffi::c_int>,
    pub free_file_info:
        Option<unsafe extern "C" fn(*mut super_block, ::core::ffi::c_int) -> ::core::ffi::c_int>,
    pub read_dqblk: Option<unsafe extern "C" fn(*mut dquot) -> ::core::ffi::c_int>,
    pub commit_dqblk: Option<unsafe extern "C" fn(*mut dquot) -> ::core::ffi::c_int>,
    pub release_dqblk: Option<unsafe extern "C" fn(*mut dquot) -> ::core::ffi::c_int>,
    pub get_next_id:
        Option<unsafe extern "C" fn(*mut super_block, *mut kqid) -> ::core::ffi::c_int>,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct kqid {
    pub c2rust_unnamed: C2Rust_Unnamed_35,
    pub r#type: quota_type,
}
pub type quota_type = ::core::ffi::c_uint;
pub const PRJQUOTA: quota_type = 2;
pub const GRPQUOTA: quota_type = 1;
pub const USRQUOTA: quota_type = 0;
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_35 {
    pub uid: kuid_t,
    pub gid: kgid_t,
    pub projid: kprojid_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct kprojid_t {
    pub val: projid_t,
}
pub type projid_t = __kernel_uid32_t;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct dquot {
    pub dq_hash: hlist_node,
    pub dq_inuse: list_head,
    pub dq_free: list_head,
    pub dq_dirty: list_head,
    pub dq_lock: mutex,
    pub dq_dqb_lock: spinlock_t,
    pub dq_count: atomic_t,
    pub dq_sb: *mut super_block,
    pub dq_id: kqid,
    pub dq_off: loff_t,
    pub dq_flags: ::core::ffi::c_ulong,
    pub dq_dqb: mem_dqblk,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct mem_dqblk {
    pub dqb_bhardlimit: qsize_t,
    pub dqb_bsoftlimit: qsize_t,
    pub dqb_curspace: qsize_t,
    pub dqb_rsvspace: qsize_t,
    pub dqb_ihardlimit: qsize_t,
    pub dqb_isoftlimit: qsize_t,
    pub dqb_curinodes: qsize_t,
    pub dqb_btime: time64_t,
    pub dqb_itime: time64_t,
}
pub type qsize_t = ::core::ffi::c_longlong;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct mem_dqinfo {
    pub dqi_format: *mut quota_format_type,
    pub dqi_fmt_id: ::core::ffi::c_int,
    pub dqi_dirty_list: list_head,
    pub dqi_flags: ::core::ffi::c_ulong,
    pub dqi_bgrace: ::core::ffi::c_uint,
    pub dqi_igrace: ::core::ffi::c_uint,
    pub dqi_max_spc_limit: qsize_t,
    pub dqi_max_ino_limit: qsize_t,
    pub dqi_priv: *mut ::core::ffi::c_void,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct quota_format_type {
    pub qf_fmt_id: ::core::ffi::c_int,
    pub qf_ops: *const quota_format_ops,
    pub qf_owner: *mut module,
    pub qf_next: *mut quota_format_type,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct mtd_info {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct backing_dev_info {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct block_device {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct mount {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct xattr_handler {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct export_operations {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct quotactl_ops {
    pub quota_on: Option<
        unsafe extern "C" fn(
            *mut super_block,
            ::core::ffi::c_int,
            ::core::ffi::c_int,
            *const path,
        ) -> ::core::ffi::c_int,
    >,
    pub quota_off:
        Option<unsafe extern "C" fn(*mut super_block, ::core::ffi::c_int) -> ::core::ffi::c_int>,
    pub quota_enable:
        Option<unsafe extern "C" fn(*mut super_block, ::core::ffi::c_uint) -> ::core::ffi::c_int>,
    pub quota_disable:
        Option<unsafe extern "C" fn(*mut super_block, ::core::ffi::c_uint) -> ::core::ffi::c_int>,
    pub quota_sync:
        Option<unsafe extern "C" fn(*mut super_block, ::core::ffi::c_int) -> ::core::ffi::c_int>,
    pub set_info: Option<
        unsafe extern "C" fn(
            *mut super_block,
            ::core::ffi::c_int,
            *mut qc_info,
        ) -> ::core::ffi::c_int,
    >,
    pub get_dqblk:
        Option<unsafe extern "C" fn(*mut super_block, kqid, *mut qc_dqblk) -> ::core::ffi::c_int>,
    pub get_nextdqblk: Option<
        unsafe extern "C" fn(*mut super_block, *mut kqid, *mut qc_dqblk) -> ::core::ffi::c_int,
    >,
    pub set_dqblk:
        Option<unsafe extern "C" fn(*mut super_block, kqid, *mut qc_dqblk) -> ::core::ffi::c_int>,
    pub get_state:
        Option<unsafe extern "C" fn(*mut super_block, *mut qc_state) -> ::core::ffi::c_int>,
    pub rm_xquota:
        Option<unsafe extern "C" fn(*mut super_block, ::core::ffi::c_uint) -> ::core::ffi::c_int>,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct qc_state {
    pub s_incoredqs: ::core::ffi::c_uint,
    pub s_state: [qc_type_state; 3],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct qc_type_state {
    pub flags: ::core::ffi::c_uint,
    pub spc_timelimit: ::core::ffi::c_uint,
    pub ino_timelimit: ::core::ffi::c_uint,
    pub rt_spc_timelimit: ::core::ffi::c_uint,
    pub spc_warnlimit: ::core::ffi::c_uint,
    pub ino_warnlimit: ::core::ffi::c_uint,
    pub rt_spc_warnlimit: ::core::ffi::c_uint,
    pub ino: ::core::ffi::c_ulonglong,
    pub blocks: blkcnt_t,
    pub nextents: blkcnt_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct qc_dqblk {
    pub d_fieldmask: ::core::ffi::c_int,
    pub d_spc_hardlimit: u64_0,
    pub d_spc_softlimit: u64_0,
    pub d_ino_hardlimit: u64_0,
    pub d_ino_softlimit: u64_0,
    pub d_space: u64_0,
    pub d_ino_count: u64_0,
    pub d_ino_timer: s64,
    pub d_spc_timer: s64,
    pub d_ino_warns: ::core::ffi::c_int,
    pub d_spc_warns: ::core::ffi::c_int,
    pub d_rt_spc_hardlimit: u64_0,
    pub d_rt_spc_softlimit: u64_0,
    pub d_rt_space: u64_0,
    pub d_rt_spc_timer: s64,
    pub d_rt_spc_warns: ::core::ffi::c_int,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct qc_info {
    pub i_fieldmask: ::core::ffi::c_int,
    pub i_flags: ::core::ffi::c_uint,
    pub i_spc_timelimit: ::core::ffi::c_uint,
    pub i_ino_timelimit: ::core::ffi::c_uint,
    pub i_rt_spc_timelimit: ::core::ffi::c_uint,
    pub i_spc_warnlimit: ::core::ffi::c_uint,
    pub i_ino_warnlimit: ::core::ffi::c_uint,
    pub i_rt_spc_warnlimit: ::core::ffi::c_uint,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct dquot_operations {
    pub write_dquot: Option<unsafe extern "C" fn(*mut dquot) -> ::core::ffi::c_int>,
    pub alloc_dquot:
        Option<unsafe extern "C" fn(*mut super_block, ::core::ffi::c_int) -> *mut dquot>,
    pub destroy_dquot: Option<unsafe extern "C" fn(*mut dquot) -> ()>,
    pub acquire_dquot: Option<unsafe extern "C" fn(*mut dquot) -> ::core::ffi::c_int>,
    pub release_dquot: Option<unsafe extern "C" fn(*mut dquot) -> ::core::ffi::c_int>,
    pub mark_dirty: Option<unsafe extern "C" fn(*mut dquot) -> ::core::ffi::c_int>,
    pub write_info:
        Option<unsafe extern "C" fn(*mut super_block, ::core::ffi::c_int) -> ::core::ffi::c_int>,
    pub get_reserved_space: Option<unsafe extern "C" fn(*mut inode) -> *mut qsize_t>,
    pub get_projid: Option<unsafe extern "C" fn(*mut inode, *mut kprojid_t) -> ::core::ffi::c_int>,
    pub get_inode_usage:
        Option<unsafe extern "C" fn(*mut inode, *mut qsize_t) -> ::core::ffi::c_int>,
    pub get_next_id:
        Option<unsafe extern "C" fn(*mut super_block, *mut kqid) -> ::core::ffi::c_int>,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct super_operations {
    pub alloc_inode: Option<unsafe extern "C" fn(*mut super_block) -> *mut inode>,
    pub destroy_inode: Option<unsafe extern "C" fn(*mut inode) -> ()>,
    pub free_inode: Option<unsafe extern "C" fn(*mut inode) -> ()>,
    pub dirty_inode: Option<unsafe extern "C" fn(*mut inode, ::core::ffi::c_int) -> ()>,
    pub write_inode:
        Option<unsafe extern "C" fn(*mut inode, *mut writeback_control) -> ::core::ffi::c_int>,
    pub drop_inode: Option<unsafe extern "C" fn(*mut inode) -> ::core::ffi::c_int>,
    pub evict_inode: Option<unsafe extern "C" fn(*mut inode) -> ()>,
    pub put_super: Option<unsafe extern "C" fn(*mut super_block) -> ()>,
    pub sync_fs:
        Option<unsafe extern "C" fn(*mut super_block, ::core::ffi::c_int) -> ::core::ffi::c_int>,
    pub freeze_super: Option<
        unsafe extern "C" fn(
            *mut super_block,
            freeze_holder,
            *const ::core::ffi::c_void,
        ) -> ::core::ffi::c_int,
    >,
    pub freeze_fs: Option<unsafe extern "C" fn(*mut super_block) -> ::core::ffi::c_int>,
    pub thaw_super: Option<
        unsafe extern "C" fn(
            *mut super_block,
            freeze_holder,
            *const ::core::ffi::c_void,
        ) -> ::core::ffi::c_int,
    >,
    pub unfreeze_fs: Option<unsafe extern "C" fn(*mut super_block) -> ::core::ffi::c_int>,
    pub statfs: Option<unsafe extern "C" fn(*mut dentry, *mut kstatfs) -> ::core::ffi::c_int>,
    pub umount_begin: Option<unsafe extern "C" fn(*mut super_block) -> ()>,
    pub show_options:
        Option<unsafe extern "C" fn(*mut seq_file, *mut dentry) -> ::core::ffi::c_int>,
    pub show_devname:
        Option<unsafe extern "C" fn(*mut seq_file, *mut dentry) -> ::core::ffi::c_int>,
    pub show_path: Option<unsafe extern "C" fn(*mut seq_file, *mut dentry) -> ::core::ffi::c_int>,
    pub show_stats: Option<unsafe extern "C" fn(*mut seq_file, *mut dentry) -> ::core::ffi::c_int>,
    pub nr_cached_objects:
        Option<unsafe extern "C" fn(*mut super_block, *mut shrink_control) -> ::core::ffi::c_long>,
    pub free_cached_objects:
        Option<unsafe extern "C" fn(*mut super_block, *mut shrink_control) -> ::core::ffi::c_long>,
    pub remove_bdev:
        Option<unsafe extern "C" fn(*mut super_block, *mut block_device) -> ::core::ffi::c_int>,
    pub shutdown: Option<unsafe extern "C" fn(*mut super_block) -> ()>,
    pub report_error: Option<unsafe extern "C" fn(*const fserror_event) -> ()>,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct fserror_event {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct kstatfs {
    _private: [u8; 0],
}
pub type freeze_holder = ::core::ffi::c_uint;
pub const FREEZE_EXCL: freeze_holder = 8;
pub const FREEZE_MAY_NEST: freeze_holder = 4;
pub const FREEZE_HOLDER_USERSPACE: freeze_holder = 2;
pub const FREEZE_HOLDER_KERNEL: freeze_holder = 1;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct writeback_control {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct file_system_type {
    pub name: *const ::core::ffi::c_char,
    pub fs_flags: ::core::ffi::c_int,
    pub init_fs_context: Option<unsafe extern "C" fn(*mut fs_context) -> ::core::ffi::c_int>,
    pub parameters: *const fs_parameter_spec,
    pub kill_sb: Option<unsafe extern "C" fn(*mut super_block) -> ()>,
    pub owner: *mut module,
    pub list: hlist_node,
    pub fs_supers: hlist_head,
    pub s_lock_key: lock_class_key,
    pub s_umount_key: lock_class_key,
    pub s_vfs_rename_key: lock_class_key,
    pub s_writers_key: [lock_class_key; 3],
    pub i_lock_key: lock_class_key,
    pub i_mutex_key: lock_class_key,
    pub invalidate_lock_key: lock_class_key,
    pub i_mutex_dir_key: lock_class_key,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct lock_class_key {}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct fs_parameter_spec {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct fs_context {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union shortname_store {
    pub string: [::core::ffi::c_uchar; 40],
    pub words: [::core::ffi::c_ulong; 5],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_36 {
    pub __d_name: qstr,
    pub d_name: qstr,
}
pub type seqcount_spinlock_t = seqcount_spinlock;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct seqcount_spinlock {
    pub seqcount: seqcount_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct fown_struct {
    pub file: *mut file,
    pub lock: rwlock_t,
    pub pid: *mut pid,
    pub pid_type: pid_type,
    pub uid: kuid_t,
    pub euid: kuid_t,
    pub signum: ::core::ffi::c_int,
}
pub type pid_type = ::core::ffi::c_uint;
pub const PIDTYPE_MAX: pid_type = 4;
pub const PIDTYPE_SID: pid_type = 3;
pub const PIDTYPE_PGID: pid_type = 2;
pub const PIDTYPE_TGID: pid_type = 1;
pub const PIDTYPE_PID: pid_type = 0;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct pid {
    pub count: refcount_t,
    pub level: ::core::ffi::c_uint,
    pub lock: spinlock_t,
    pub c2rust_unnamed: C2Rust_Unnamed_37,
    pub tasks: [hlist_head; 4],
    pub inodes: hlist_head,
    pub wait_pidfd: wait_queue_head_t,
    pub rcu: callback_head,
    pub numbers: [upid; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct upid {
    pub nr: ::core::ffi::c_int,
    pub ns: *mut pid_namespace,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct pid_namespace {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_37 {
    pub ino: u64_0,
    pub pidfs_hash: rhash_head,
    pub stashed: *mut dentry,
    pub attr: *mut pidfs_attr,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct pidfs_attr {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct rhash_head {
    pub next: *mut rhash_head,
}
pub type rwlock_t = rwlock;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct rwlock {
    pub raw_lock: arch_rwlock_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct arch_rwlock_t {}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct cred {
    pub usage: atomic_long_t,
    pub uid: kuid_t,
    pub gid: kgid_t,
    pub suid: kuid_t,
    pub sgid: kgid_t,
    pub euid: kuid_t,
    pub egid: kgid_t,
    pub fsuid: kuid_t,
    pub fsgid: kgid_t,
    pub securebits: ::core::ffi::c_uint,
    pub cap_inheritable: kernel_cap_t,
    pub cap_permitted: kernel_cap_t,
    pub cap_effective: kernel_cap_t,
    pub cap_bset: kernel_cap_t,
    pub cap_ambient: kernel_cap_t,
    pub user: *mut user_struct,
    pub user_ns: *mut user_namespace,
    pub ucounts: *mut ucounts,
    pub group_info: *mut group_info,
    pub c2rust_unnamed: C2Rust_Unnamed_38,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_38 {
    pub non_rcu: ::core::ffi::c_int,
    pub rcu: callback_head,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct group_info {
    pub usage: refcount_t,
    pub ngroups: ::core::ffi::c_int,
    pub gid: [kgid_t; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct ucounts {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct user_struct {
    pub __count: refcount_t,
    pub unix_inflight: ::core::ffi::c_ulong,
    pub pipe_bufs: atomic_long_t,
    pub uidhash_node: hlist_node,
    pub uid: kuid_t,
    pub locked_vm: atomic_long_t,
    pub ratelimit: ratelimit_state,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct ratelimit_state {
    pub lock: raw_spinlock_t,
    pub interval: ::core::ffi::c_int,
    pub burst: ::core::ffi::c_int,
    pub rs_n_left: atomic_t,
    pub missed: atomic_t,
    pub flags: ::core::ffi::c_uint,
    pub begin: ::core::ffi::c_ulong,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct kernel_cap_t {
    pub val: u64_0,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct swap_info_struct {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct folio {
    pub c2rust_unnamed: C2Rust_Unnamed_48,
    pub c2rust_unnamed_0: C2Rust_Unnamed_43,
    pub c2rust_unnamed_1: C2Rust_Unnamed_41,
    pub c2rust_unnamed_2: C2Rust_Unnamed_39,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_39 {
    pub c2rust_unnamed: C2Rust_Unnamed_40,
    pub __page_3: page,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_40 {
    pub _flags_3: ::core::ffi::c_ulong,
    pub _head_3: ::core::ffi::c_ulong,
    pub _hugetlb_subpool: *mut ::core::ffi::c_void,
    pub _hugetlb_cgroup: *mut ::core::ffi::c_void,
    pub _hugetlb_cgroup_rsvd: *mut ::core::ffi::c_void,
    pub _hugetlb_hwpoison: *mut ::core::ffi::c_void,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_41 {
    pub c2rust_unnamed: C2Rust_Unnamed_42,
    pub __page_2: page,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_42 {
    pub _flags_2: ::core::ffi::c_ulong,
    pub _head_2: ::core::ffi::c_ulong,
    pub _deferred_list: list_head,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_43 {
    pub c2rust_unnamed: C2Rust_Unnamed_44,
    pub __page_1: page,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_44 {
    pub _flags_1: ::core::ffi::c_ulong,
    pub _head_1: ::core::ffi::c_ulong,
    pub c2rust_unnamed: C2Rust_Unnamed_45,
    pub _mapcount_1: atomic_t,
    pub _refcount_1: atomic_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_45 {
    pub c2rust_unnamed: C2Rust_Unnamed_46,
    pub _usable_1: [::core::ffi::c_ulong; 4],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_46 {
    pub _large_mapcount: atomic_t,
    pub _nr_pages_mapped: atomic_t,
    pub _entire_mapcount: atomic_t,
    pub _pincount: atomic_t,
    pub _mm_id_mapcount: [mm_id_mapcount_t; 2],
    pub c2rust_unnamed: C2Rust_Unnamed_47,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_47 {
    pub _mm_id: [mm_id_t; 2],
    pub _mm_ids: ::core::ffi::c_ulong,
}
pub type mm_id_t = ::core::ffi::c_uint;
pub type mm_id_mapcount_t = ::core::ffi::c_int;
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_48 {
    pub c2rust_unnamed: C2Rust_Unnamed_49,
    pub page: page,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_49 {
    pub flags: memdesc_flags_t,
    pub c2rust_unnamed: C2Rust_Unnamed_52,
    pub mapping: *mut address_space,
    pub c2rust_unnamed_0: C2Rust_Unnamed_51,
    pub c2rust_unnamed_1: C2Rust_Unnamed_50,
    pub _mapcount: atomic_t,
    pub _refcount: atomic_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_50 {
    pub private: *mut ::core::ffi::c_void,
    pub swap: swp_entry_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct swp_entry_t {
    pub val: ::core::ffi::c_ulong,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_51 {
    pub index: ::core::ffi::c_ulong,
    pub share: ::core::ffi::c_ulong,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_52 {
    pub lru: list_head,
    pub c2rust_unnamed: C2Rust_Unnamed_53,
    pub pgmap: *mut dev_pagemap,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct dev_pagemap {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_53 {
    pub __filler: *mut ::core::ffi::c_void,
    pub mlock_count: ::core::ffi::c_uint,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct memdesc_flags_t {
    pub f: ::core::ffi::c_ulong,
}
pub type migrate_mode = ::core::ffi::c_uint;
pub const MIGRATE_SYNC: migrate_mode = 2;
pub const MIGRATE_SYNC_LIGHT: migrate_mode = 1;
pub const MIGRATE_ASYNC: migrate_mode = 0;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct readahead_control {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct rb_root_cached {
    pub rb_root: rb_root,
    pub rb_leftmost: *mut rb_node,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct rb_root {
    pub rb_node: *mut rb_node,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct xarray {
    pub xa_lock: spinlock_t,
    pub xa_flags: gfp_t,
    pub xa_head: *mut ::core::ffi::c_void,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_54 {
    pub lru: list_head,
    pub buddy_list: list_head,
    pub pcp_list: list_head,
    pub pcp_llist: llist_node,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct tlbflush_unmap_batch {
    pub arch: arch_tlbflush_unmap_batch,
    pub flush_required: bool_0,
    pub writable: bool_0,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct arch_tlbflush_unmap_batch {
    pub cpumask: cpumask,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct cpumask {
    pub bits: [::core::ffi::c_ulong; 1],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct sched_mm_cid {}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct rseq_data {}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct perf_ctx_data {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct perf_event_context {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct futex_sched_data {}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct task_io_accounting {}
pub type kernel_siginfo_t = kernel_siginfo;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct kernel_siginfo {
    pub c2rust_unnamed: C2Rust_Unnamed_55,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_55 {
    pub si_signo: ::core::ffi::c_int,
    pub si_errno: ::core::ffi::c_int,
    pub si_code: ::core::ffi::c_int,
    pub _sifields: __sifields,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union __sifields {
    pub _kill: C2Rust_Unnamed_66,
    pub _timer: C2Rust_Unnamed_65,
    pub _rt: C2Rust_Unnamed_64,
    pub _sigchld: C2Rust_Unnamed_63,
    pub _sigfault: C2Rust_Unnamed_58,
    pub _sigpoll: C2Rust_Unnamed_57,
    pub _sigsys: C2Rust_Unnamed_56,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_56 {
    pub _call_addr: *mut ::core::ffi::c_void,
    pub _syscall: ::core::ffi::c_int,
    pub _arch: ::core::ffi::c_uint,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_57 {
    pub _band: ::core::ffi::c_long,
    pub _fd: ::core::ffi::c_int,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_58 {
    pub _addr: *mut ::core::ffi::c_void,
    pub c2rust_unnamed: C2Rust_Unnamed_59,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_59 {
    pub _trapno: ::core::ffi::c_int,
    pub _addr_lsb: ::core::ffi::c_short,
    pub _addr_bnd: C2Rust_Unnamed_62,
    pub _addr_pkey: C2Rust_Unnamed_61,
    pub _perf: C2Rust_Unnamed_60,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_60 {
    pub _data: ::core::ffi::c_ulong,
    pub _type: __u32,
    pub _flags: __u32,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_61 {
    pub _dummy_pkey: [::core::ffi::c_char; 8],
    pub _pkey: __u32,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_62 {
    pub _dummy_bnd: [::core::ffi::c_char; 8],
    pub _lower: *mut ::core::ffi::c_void,
    pub _upper: *mut ::core::ffi::c_void,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_63 {
    pub _pid: __kernel_pid_t,
    pub _uid: __kernel_uid32_t,
    pub _status: ::core::ffi::c_int,
    pub _utime: __kernel_clock_t,
    pub _stime: __kernel_clock_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_64 {
    pub _pid: __kernel_pid_t,
    pub _uid: __kernel_uid32_t,
    pub _sigval: sigval_t,
}
pub type sigval_t = sigval;
#[derive(Copy, Clone)]
#[repr(C)]
pub union sigval {
    pub sival_int: ::core::ffi::c_int,
    pub sival_ptr: *mut ::core::ffi::c_void,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_65 {
    pub _tid: __kernel_timer_t,
    pub _overrun: ::core::ffi::c_int,
    pub _sigval: sigval_t,
    pub _sys_private: ::core::ffi::c_int,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_66 {
    pub _pid: __kernel_pid_t,
    pub _uid: __kernel_uid32_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct io_context {
    pub refcount: atomic_long_t,
    pub active_ref: atomic_t,
    pub ioprio: ::core::ffi::c_ushort,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct reclaim_state {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct blk_plug {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct bio_list {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct wake_q_node {
    pub next: *mut wake_q_node,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct syscall_user_dispatch {
    pub selector: *mut ::core::ffi::c_char,
    pub offset: ::core::ffi::c_ulong,
    pub len: ::core::ffi::c_ulong,
    pub on_dispatch: bool_0,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct seccomp {}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct sigpending {
    pub list: list_head,
    pub signal: sigset_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct sigset_t {
    pub sig: [::core::ffi::c_ulong; 1],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct sighand_struct {
    pub siglock: spinlock_t,
    pub count: refcount_t,
    pub signalfd_wqh: wait_queue_head_t,
    pub action: [k_sigaction; 64],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct k_sigaction {
    pub sa: sigaction,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct sigaction {
    pub sa_handler: __sighandler_t,
    pub sa_flags: ::core::ffi::c_ulong,
    pub sa_mask: sigset_t,
}
pub type __sighandler_t = Option<__signalfn_t>;
pub type __signalfn_t = unsafe extern "C" fn(::core::ffi::c_int) -> ();
#[derive(Copy, Clone)]
#[repr(C)]
pub struct rlimit {
    pub rlim_cur: __kernel_ulong_t,
    pub rlim_max: __kernel_ulong_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct prev_cputime {
    pub utime: u64_0,
    pub stime: u64_0,
    pub lock: raw_spinlock_t,
}
pub type seqlock_t = seqlock;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct seqlock {
    pub seqcount: seqcount_spinlock_t,
    pub lock: spinlock_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct tty_struct {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct posix_cputimers {}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct core_state {
    pub nr_threads: atomic_t,
    pub dumper: core_thread,
    pub startup: completion,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct core_thread {
    pub task: *mut task_struct,
    pub next: *mut core_thread,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct nsproxy {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct files_struct {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct fs_struct {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct nameidata {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct restart_block {
    pub arch_data: ::core::ffi::c_ulong,
    pub r#fn: Option<unsafe extern "C" fn(*mut restart_block) -> ::core::ffi::c_long>,
    pub c2rust_unnamed: C2Rust_Unnamed_67,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_67 {
    pub futex: C2Rust_Unnamed_71,
    pub nanosleep: C2Rust_Unnamed_69,
    pub poll: C2Rust_Unnamed_68,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_68 {
    pub ufds: *mut pollfd,
    pub nfds: ::core::ffi::c_int,
    pub has_timeout: ::core::ffi::c_int,
    pub end_time: timespec64,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct pollfd {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_69 {
    pub clockid: clockid_t,
    pub r#type: timespec_type,
    pub c2rust_unnamed: C2Rust_Unnamed_70,
    pub expires: ktime_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_70 {
    pub rmtp: *mut __kernel_timespec,
    pub compat_rmtp: *mut old_timespec32,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct old_timespec32 {
    pub tv_sec: old_time32_t,
    pub tv_nsec: s32,
}
pub type old_time32_t = s32;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct __kernel_timespec {
    pub tv_sec: __kernel_time64_t,
    pub tv_nsec: ::core::ffi::c_longlong,
}
pub type timespec_type = ::core::ffi::c_uint;
pub const TT_COMPAT: timespec_type = 2;
pub const TT_NATIVE: timespec_type = 1;
pub const TT_NONE: timespec_type = 0;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_Unnamed_71 {
    pub uaddr: *mut u32_0,
    pub val: u32_0,
    pub flags: u32_0,
    pub bitset: u32_0,
    pub time: ktime_t,
    pub uaddr2: *mut u32_0,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct task_exec_state {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct plist_node {
    pub prio: ::core::ffi::c_int,
    pub prio_list: list_head,
    pub node_list: list_head,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct sched_info {}
pub type cpumask_t = cpumask;
#[derive(Copy, Clone)]
#[repr(C, align(64))]
pub struct sched_statistics(pub C2Rust_sched_statistics_Inner);
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_sched_statistics_Inner {}
#[allow(dead_code, non_upper_case_globals)]
const C2Rust_sched_statistics_PADDING: usize = ::core::mem::size_of::<sched_statistics>()
    - ::core::mem::size_of::<C2Rust_sched_statistics_Inner>();
#[derive(Copy, Clone)]
#[repr(C)]
pub struct sched_class {
    _private: [u8; 0],
}
pub type dl_server_pick_f =
    Option<unsafe extern "C" fn(*mut sched_dl_entity, *mut rq_flags) -> *mut task_struct>;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct rq_flags {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct rq {
    _private: [u8; 0],
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct hrtimer {
    pub node: timerqueue_linked_node,
    pub base: *mut hrtimer_clock_base,
    pub is_queued: bool_0,
    pub is_rel: bool_0,
    pub is_soft: bool_0,
    pub is_hard: bool_0,
    pub is_lazy: bool_0,
    pub _softexpires: ktime_t,
    pub function: Option<unsafe extern "C" fn(*mut hrtimer) -> hrtimer_restart>,
}
pub type hrtimer_restart = ::core::ffi::c_uint;
pub const HRTIMER_RESTART: hrtimer_restart = 1;
pub const HRTIMER_NORESTART: hrtimer_restart = 0;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct hrtimer_clock_base {
    pub cpu_base: *mut hrtimer_cpu_base,
    pub index: ::core::ffi::c_uint,
    pub clockid: clockid_t,
    pub seq: seqcount_raw_spinlock_t,
    pub expires_next: ktime_t,
    pub running: *mut hrtimer,
    pub active: timerqueue_linked_head,
    pub offset: ktime_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct timerqueue_linked_head {
    pub rb_root: rb_root_linked,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct rb_root_linked {
    pub rb_root: rb_root,
    pub rb_leftmost: *mut rb_node_linked,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct rb_node_linked {
    pub node: rb_node,
    pub prev: *mut rb_node_linked,
    pub next: *mut rb_node_linked,
}
pub type seqcount_raw_spinlock_t = seqcount_raw_spinlock;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct seqcount_raw_spinlock {
    pub seqcount: seqcount_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct hrtimer_cpu_base {
    pub lock: raw_spinlock_t,
    pub cpu: ::core::ffi::c_uint,
    pub active_bases: ::core::ffi::c_uint,
    pub clock_was_set_seq: ::core::ffi::c_uint,
    pub hres_active: bool_0,
    pub deferred_rearm: bool_0,
    pub deferred_needs_update: bool_0,
    pub hang_detected: bool_0,
    pub softirq_activated: bool_0,
    pub online: bool_0,
    pub expires_next: ktime_t,
    pub next_timer: *mut hrtimer,
    pub softirq_expires_next: ktime_t,
    pub softirq_next_timer: *mut hrtimer,
    pub deferred_expires_next: ktime_t,
    pub clock_base: [hrtimer_clock_base; 8],
    pub csd: call_single_data_t,
}
pub type call_single_data_t = __call_single_data;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct __call_single_data {
    pub node: __call_single_node,
    pub func: smp_call_func_t,
    pub info: *mut ::core::ffi::c_void,
}
pub type smp_call_func_t = Option<unsafe extern "C" fn(*mut ::core::ffi::c_void) -> ()>;
#[derive(Copy, Clone)]
#[repr(C)]
pub struct __call_single_node {
    pub llist: llist_node,
    pub c2rust_unnamed: C2Rust_Unnamed_72,
    pub src: u16_0,
    pub dst: u16_0,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub union C2Rust_Unnamed_72 {
    pub u_flags: ::core::ffi::c_uint,
    pub a_flags: atomic_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct timerqueue_linked_node {
    pub node: rb_node_linked,
    pub expires: ktime_t,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct sched_rt_entity {
    pub run_list: list_head,
    pub timeout: ::core::ffi::c_ulong,
    pub watchdog_stamp: ::core::ffi::c_ulong,
    pub time_slice: ::core::ffi::c_uint,
    pub on_rq: ::core::ffi::c_ushort,
    pub on_list: ::core::ffi::c_ushort,
    pub back: *mut sched_rt_entity,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct sched_entity {
    pub load: load_weight,
    pub run_node: rb_node,
    pub deadline: u64_0,
    pub min_vruntime: u64_0,
    pub min_slice: u64_0,
    pub max_slice: u64_0,
    pub group_node: list_head,
    pub on_rq: ::core::ffi::c_uchar,
    pub sched_delayed: ::core::ffi::c_uchar,
    pub rel_deadline: ::core::ffi::c_uchar,
    pub custom_slice: ::core::ffi::c_uchar,
    pub exec_start: u64_0,
    pub sum_exec_runtime: u64_0,
    pub prev_sum_exec_runtime: u64_0,
    pub vruntime: u64_0,
    pub vlag: s64,
    pub vprot: u64_0,
    pub slice: u64_0,
    pub nr_migrations: u64_0,
    pub avg: sched_avg,
}
#[derive(Copy, Clone)]
#[repr(C, align(64))]
pub struct sched_avg(pub C2Rust_sched_avg_Inner);
#[derive(Copy, Clone)]
#[repr(C)]
pub struct C2Rust_sched_avg_Inner {
    pub last_update_time: u64_0,
    pub load_sum: u64_0,
    pub runnable_sum: u64_0,
    pub util_sum: u32_0,
    pub period_contrib: u32_0,
    pub load_avg: ::core::ffi::c_ulong,
    pub runnable_avg: ::core::ffi::c_ulong,
    pub util_avg: ::core::ffi::c_ulong,
    pub util_est: ::core::ffi::c_uint,
}
#[allow(dead_code, non_upper_case_globals)]
const C2Rust_sched_avg_PADDING: usize =
    ::core::mem::size_of::<sched_avg>() - ::core::mem::size_of::<C2Rust_sched_avg_Inner>();
#[derive(Copy, Clone)]
#[repr(C)]
pub struct load_weight {
    pub weight: ::core::ffi::c_ulong,
    pub inv_weight: u32_0,
}
#[derive(Copy, Clone)]
#[repr(C)]
pub struct thread_info {
    pub flags: ::core::ffi::c_ulong,
    pub preempt_count: ::core::ffi::c_int,
    pub kernel_sp: ::core::ffi::c_long,
    pub user_sp: ::core::ffi::c_long,
    pub cpu: ::core::ffi::c_int,
    pub syscall_work: ::core::ffi::c_ulong,
    pub a0: ::core::ffi::c_ulong,
    pub a1: ::core::ffi::c_ulong,
    pub a2: ::core::ffi::c_ulong,
}
pub type C2Rust_Unnamed_73 = ::core::ffi::c_uint;
pub const ___GFP_LAST_BIT: C2Rust_Unnamed_73 = 25;
pub const ___GFP_NO_OBJ_EXT_BIT: C2Rust_Unnamed_73 = 24;
pub const ___GFP_ZEROTAGS_BIT: C2Rust_Unnamed_73 = 23;
pub const ___GFP_THISNODE_BIT: C2Rust_Unnamed_73 = 21;
pub const ___GFP_HARDWALL_BIT: C2Rust_Unnamed_73 = 20;
pub const ___GFP_NOMEMALLOC_BIT: C2Rust_Unnamed_73 = 19;
pub const ___GFP_COMP_BIT: C2Rust_Unnamed_73 = 18;
pub const ___GFP_MEMALLOC_BIT: C2Rust_Unnamed_73 = 17;
pub const ___GFP_NORETRY_BIT: C2Rust_Unnamed_73 = 16;
pub const ___GFP_NOFAIL_BIT: C2Rust_Unnamed_73 = 15;
pub const ___GFP_RETRY_MAYFAIL_BIT: C2Rust_Unnamed_73 = 14;
pub const ___GFP_NOWARN_BIT: C2Rust_Unnamed_73 = 13;
pub const ___GFP_WRITE_BIT: C2Rust_Unnamed_73 = 12;
pub const ___GFP_UNUSED_BIT: C2Rust_Unnamed_73 = 9;
pub const ___GFP_ZERO_BIT: C2Rust_Unnamed_73 = 8;
pub const ___GFP_HIGH_BIT: C2Rust_Unnamed_73 = 5;
pub const ___GFP_MOVABLE_BIT: C2Rust_Unnamed_73 = 3;
pub const ___GFP_DMA32_BIT: C2Rust_Unnamed_73 = 2;
pub const ___GFP_HIGHMEM_BIT: C2Rust_Unnamed_73 = 1;
pub const NULL: *mut ::core::ffi::c_void = ::core::ptr::null_mut::<::core::ffi::c_void>();
pub const INT_MAX: ::core::ffi::c_int =
    (!(0 as ::core::ffi::c_uint) >> 1 as ::core::ffi::c_int) as ::core::ffi::c_int;
pub const PAGE_SHIFT: ::core::ffi::c_int = CONFIG_PAGE_SHIFT;
#[inline]
unsafe extern "C" fn mem_alloc_profiling_enabled() -> bool_0 {
    unsafe {
        return r#false as ::core::ffi::c_int != 0;
    }
}
pub const CRC32_POLY_BE: ::core::ffi::c_int = 0x4c11db7 as ::core::ffi::c_int;
pub const KMALLOC_SHIFT_HIGH: ::core::ffi::c_int = PAGE_SHIFT + 1 as ::core::ffi::c_int;
pub const KMALLOC_SHIFT_LOW: ::core::ffi::c_int = 3 as ::core::ffi::c_int;
pub const KMALLOC_MAX_CACHE_SIZE: ::core::ffi::c_ulong =
    (1 as ::core::ffi::c_ulong) << KMALLOC_SHIFT_HIGH;
pub const KMALLOC_MIN_SIZE: ::core::ffi::c_int = (1 as ::core::ffi::c_int) << KMALLOC_SHIFT_LOW;
#[inline(always)]
unsafe extern "C" fn kmalloc_type(
    mut flags: gfp_t,
    mut token: kmalloc_token_t,
) -> kmalloc_cache_type {
    unsafe {
        if (flags
            & (((1 as ::core::ffi::c_ulong) << ___GFP_RECLAIMABLE_BIT as ::core::ffi::c_int)
                as gfp_t
                | (if false {
                    ((1 as ::core::ffi::c_ulong) << ___GFP_DMA_BIT as ::core::ffi::c_int) as gfp_t
                } else {
                    0 as gfp_t
                })
                | (if false {
                    ((1 as ::core::ffi::c_ulong) << ___GFP_ACCOUNT_BIT as ::core::ffi::c_int)
                        as gfp_t
                } else {
                    0 as gfp_t
                }))
            == 0 as gfp_t) as ::core::ffi::c_int as ::core::ffi::c_long
            != 0
        {
            return KMALLOC_NORMAL;
        }
        if false
            && flags
                & ((1 as ::core::ffi::c_ulong) << ___GFP_DMA_BIT as ::core::ffi::c_int) as gfp_t
                != 0
        {
            return KMALLOC_DMA;
        }
        if true
            || flags
                & ((1 as ::core::ffi::c_ulong) << ___GFP_RECLAIMABLE_BIT as ::core::ffi::c_int)
                    as gfp_t
                != 0
        {
            return KMALLOC_RECLAIM;
        } else {
            return KMALLOC_CGROUP;
        };
    }
}
#[inline(always)]
unsafe extern "C" fn __kmalloc_index(
    mut size: size_t,
    mut size_is_constant: bool_0,
) -> ::core::ffi::c_uint {
    unsafe {
        if size == 0 {
            return 0 as ::core::ffi::c_uint;
        }
        if size <= KMALLOC_MIN_SIZE as size_t {
            return KMALLOC_SHIFT_LOW as ::core::ffi::c_uint;
        }
        if KMALLOC_MIN_SIZE <= 32 as ::core::ffi::c_int
            && size > 64 as size_t
            && size <= 96 as size_t
        {
            return 1 as ::core::ffi::c_uint;
        }
        if KMALLOC_MIN_SIZE <= 64 as ::core::ffi::c_int
            && size > 128 as size_t
            && size <= 192 as size_t
        {
            return 2 as ::core::ffi::c_uint;
        }
        if size <= 8 as size_t {
            return 3 as ::core::ffi::c_uint;
        }
        if size <= 16 as size_t {
            return 4 as ::core::ffi::c_uint;
        }
        if size <= 32 as size_t {
            return 5 as ::core::ffi::c_uint;
        }
        if size <= 64 as size_t {
            return 6 as ::core::ffi::c_uint;
        }
        if size <= 128 as size_t {
            return 7 as ::core::ffi::c_uint;
        }
        if size <= 256 as size_t {
            return 8 as ::core::ffi::c_uint;
        }
        if size <= 512 as size_t {
            return 9 as ::core::ffi::c_uint;
        }
        if size <= 1024 as size_t {
            return 10 as ::core::ffi::c_uint;
        }
        if size <= (2 as ::core::ffi::c_int * 1024 as ::core::ffi::c_int) as size_t {
            return 11 as ::core::ffi::c_uint;
        }
        if size <= (4 as ::core::ffi::c_int * 1024 as ::core::ffi::c_int) as size_t {
            return 12 as ::core::ffi::c_uint;
        }
        if size <= (8 as ::core::ffi::c_int * 1024 as ::core::ffi::c_int) as size_t {
            return 13 as ::core::ffi::c_uint;
        }
        if size <= (16 as ::core::ffi::c_int * 1024 as ::core::ffi::c_int) as size_t {
            return 14 as ::core::ffi::c_uint;
        }
        if size <= (32 as ::core::ffi::c_int * 1024 as ::core::ffi::c_int) as size_t {
            return 15 as ::core::ffi::c_uint;
        }
        if size <= (64 as ::core::ffi::c_int * 1024 as ::core::ffi::c_int) as size_t {
            return 16 as ::core::ffi::c_uint;
        }
        if size <= (128 as ::core::ffi::c_int * 1024 as ::core::ffi::c_int) as size_t {
            return 17 as ::core::ffi::c_uint;
        }
        if size <= (256 as ::core::ffi::c_int * 1024 as ::core::ffi::c_int) as size_t {
            return 18 as ::core::ffi::c_uint;
        }
        if size <= (512 as ::core::ffi::c_int * 1024 as ::core::ffi::c_int) as size_t {
            return 19 as ::core::ffi::c_uint;
        }
        if size <= (1024 as ::core::ffi::c_int * 1024 as ::core::ffi::c_int) as size_t {
            return 20 as ::core::ffi::c_uint;
        }
        if size
            <= (2 as ::core::ffi::c_int * 1024 as ::core::ffi::c_int * 1024 as ::core::ffi::c_int)
                as size_t
        {
            return 21 as ::core::ffi::c_uint;
        }
        if true && size_is_constant as ::core::ffi::c_int != 0 {
            extern "C" {
                #[link_name = "__compiletime_assert_259"]
                fn __compiletime_assert_259_0() -> !;
            }
            if true {
                __compiletime_assert_259_0();
            }
        } else {
            asm!("ebreak\n", "\n", options(preserves_flags));
            unreachable!();
        }
        return -1 as ::core::ffi::c_int as ::core::ffi::c_uint;
    }
}
#[inline(always)]
unsafe extern "C" fn _kmalloc_noprof(
    mut size: size_t,
    mut flags: gfp_t,
    mut token: kmalloc_token_t,
) -> *mut ::core::ffi::c_void {
    unsafe {
        if false && size != 0 {
            let mut index: ::core::ffi::c_uint = 0;
            if size > KMALLOC_MAX_CACHE_SIZE {
                return __kmalloc_large_noprof(size, flags);
            }
            index = __kmalloc_index(size, r#true as ::core::ffi::c_int != 0);
            return __kmalloc_cache_noprof(
                kmalloc_caches[kmalloc_type(flags, token) as usize][index as usize],
                flags,
                size,
            );
        }
        return __kmalloc_noprof(size as size_t, flags);
    }
}
pub const MAX_GROUPS: ::core::ffi::c_int = 6 as ::core::ffi::c_int;
pub const GROUP_SIZE: ::core::ffi::c_int = 50 as ::core::ffi::c_int;
pub const MAX_HUFCODE_BITS: ::core::ffi::c_int = 20 as ::core::ffi::c_int;
pub const MAX_SYMBOLS: ::core::ffi::c_int = 258 as ::core::ffi::c_int;
pub const SYMBOL_RUNB: ::core::ffi::c_int = 1 as ::core::ffi::c_int;
pub const RETVAL_OK: ::core::ffi::c_int = 0 as ::core::ffi::c_int;
pub const RETVAL_LAST_BLOCK: ::core::ffi::c_int = -1 as ::core::ffi::c_int;
pub const RETVAL_NOT_BZIP_DATA: ::core::ffi::c_int = -2 as ::core::ffi::c_int;
pub const RETVAL_UNEXPECTED_INPUT_EOF: ::core::ffi::c_int = -3 as ::core::ffi::c_int;
pub const RETVAL_UNEXPECTED_OUTPUT_EOF: ::core::ffi::c_int = -4 as ::core::ffi::c_int;
pub const RETVAL_DATA_ERROR: ::core::ffi::c_int = -5 as ::core::ffi::c_int;
pub const RETVAL_OUT_OF_MEMORY: ::core::ffi::c_int = -6 as ::core::ffi::c_int;
pub const RETVAL_OBSOLETE_INPUT: ::core::ffi::c_int = -7 as ::core::ffi::c_int;
pub const BZIP2_IOBUF_SIZE: ::core::ffi::c_int = 4096 as ::core::ffi::c_int;
#[link_section = ".init.text"]
#[cold]
unsafe extern "C" fn get_bits(
    mut bd: *mut bunzip_data,
    mut bits_wanted: ::core::ffi::c_char,
) -> ::core::ffi::c_uint {
    unsafe {
        let mut bits: ::core::ffi::c_uint = 0 as ::core::ffi::c_uint;
        while (*bd).inbufBitCount < bits_wanted as ::core::ffi::c_uint {
            if (*bd).inbufPos == (*bd).inbufCount {
                if (*bd).io_error != 0 {
                    return 0 as ::core::ffi::c_uint;
                }
                (*bd).inbufCount = (*bd).fill.expect("non-null function pointer")(
                    (*bd).inbuf as *mut ::core::ffi::c_void,
                    BZIP2_IOBUF_SIZE as ::core::ffi::c_ulong,
                );
                if (*bd).inbufCount <= 0 as ::core::ffi::c_long {
                    (*bd).io_error = RETVAL_UNEXPECTED_INPUT_EOF;
                    return 0 as ::core::ffi::c_uint;
                }
                (*bd).inbufPos = 0 as ::core::ffi::c_long;
            }
            if (*bd).inbufBitCount >= 24 as ::core::ffi::c_uint {
                bits = ((*bd).inbufBits as ::core::ffi::c_ulonglong
                    & ((1 as ::core::ffi::c_ulonglong) << (*bd).inbufBitCount)
                        .wrapping_sub(1 as ::core::ffi::c_ulonglong))
                    as ::core::ffi::c_uint;
                bits_wanted = (bits_wanted as ::core::ffi::c_uint).wrapping_sub((*bd).inbufBitCount)
                    as ::core::ffi::c_char;
                bits <<= bits_wanted as ::core::ffi::c_int;
                (*bd).inbufBitCount = 0 as ::core::ffi::c_uint;
            }
            let c2rust_fresh10 = (*bd).inbufPos;
            (*bd).inbufPos = (*bd).inbufPos + 1;
            (*bd).inbufBits = (*bd).inbufBits << 8 as ::core::ffi::c_int
                | *(*bd).inbuf.offset(c2rust_fresh10 as isize) as ::core::ffi::c_uint;
            (*bd).inbufBitCount = (*bd).inbufBitCount.wrapping_add(8 as ::core::ffi::c_uint);
        }
        (*bd).inbufBitCount = (*bd)
            .inbufBitCount
            .wrapping_sub(bits_wanted as ::core::ffi::c_uint);
        bits = (bits as ::core::ffi::c_ulonglong
            | ((*bd).inbufBits >> (*bd).inbufBitCount) as ::core::ffi::c_ulonglong
                & ((1 as ::core::ffi::c_ulonglong) << bits_wanted as ::core::ffi::c_int)
                    .wrapping_sub(1 as ::core::ffi::c_ulonglong))
            as ::core::ffi::c_uint;
        return bits;
    }
}
#[link_section = ".init.text"]
#[cold]
unsafe extern "C" fn get_next_block(mut bd: *mut bunzip_data) -> ::core::ffi::c_int {
    unsafe {
        let mut hufGroup: *mut group_data = ::core::ptr::null_mut::<group_data>();
        let mut base: *mut ::core::ffi::c_int = ::core::ptr::null_mut::<::core::ffi::c_int>();
        let mut limit: *mut ::core::ffi::c_int = ::core::ptr::null_mut::<::core::ffi::c_int>();
        let mut dbufCount: ::core::ffi::c_int = 0;
        let mut nextSym: ::core::ffi::c_int = 0;
        let mut dbufSize: ::core::ffi::c_int = 0;
        let mut groupCount: ::core::ffi::c_int = 0;
        let mut selector: ::core::ffi::c_int = 0;
        let mut i: ::core::ffi::c_int = 0;
        let mut j: ::core::ffi::c_int = 0;
        let mut k: ::core::ffi::c_int = 0;
        let mut t: ::core::ffi::c_int = 0;
        let mut runPos: ::core::ffi::c_int = 0;
        let mut symCount: ::core::ffi::c_int = 0;
        let mut symTotal: ::core::ffi::c_int = 0;
        let mut nSelectors: ::core::ffi::c_int = 0;
        let mut byteCount: *mut ::core::ffi::c_int = ::core::ptr::null_mut::<::core::ffi::c_int>();
        let mut uc: ::core::ffi::c_uchar = 0;
        let mut symToByte: *mut ::core::ffi::c_uchar =
            ::core::ptr::null_mut::<::core::ffi::c_uchar>();
        let mut mtfSymbol: *mut ::core::ffi::c_uchar =
            ::core::ptr::null_mut::<::core::ffi::c_uchar>();
        let mut selectors: *mut ::core::ffi::c_uchar =
            ::core::ptr::null_mut::<::core::ffi::c_uchar>();
        let mut dbuf: *mut ::core::ffi::c_uint = ::core::ptr::null_mut::<::core::ffi::c_uint>();
        let mut origPtr: ::core::ffi::c_uint = 0;
        dbuf = (*bd).dbuf;
        dbufSize = (*bd).dbufSize as ::core::ffi::c_int;
        selectors = &raw mut (*bd).selectors as *mut ::core::ffi::c_uchar;
        byteCount = &raw mut (*bd).byteCount as *mut ::core::ffi::c_int;
        symToByte = &raw mut (*bd).symToByte as *mut ::core::ffi::c_uchar;
        mtfSymbol = &raw mut (*bd).mtfSymbol as *mut ::core::ffi::c_uchar;
        i = get_bits(bd, 24 as ::core::ffi::c_char) as ::core::ffi::c_int;
        j = get_bits(bd, 24 as ::core::ffi::c_char) as ::core::ffi::c_int;
        (*bd).headerCRC = get_bits(bd, 32 as ::core::ffi::c_char);
        if i == 0x177245 as ::core::ffi::c_int && j == 0x385090 as ::core::ffi::c_int {
            return RETVAL_LAST_BLOCK;
        }
        if i != 0x314159 as ::core::ffi::c_int || j != 0x265359 as ::core::ffi::c_int {
            return RETVAL_NOT_BZIP_DATA;
        }
        if get_bits(bd, 1 as ::core::ffi::c_char) != 0 {
            return RETVAL_OBSOLETE_INPUT;
        }
        origPtr = get_bits(bd, 24 as ::core::ffi::c_char);
        if origPtr >= dbufSize as ::core::ffi::c_uint {
            return RETVAL_DATA_ERROR;
        }
        t = get_bits(bd, 16 as ::core::ffi::c_char) as ::core::ffi::c_int;
        symTotal = 0 as ::core::ffi::c_int;
        i = 0 as ::core::ffi::c_int;
        while i < 16 as ::core::ffi::c_int {
            if t & (1 as ::core::ffi::c_int) << 15 as ::core::ffi::c_int - i != 0 {
                k = get_bits(bd, 16 as ::core::ffi::c_char) as ::core::ffi::c_int;
                j = 0 as ::core::ffi::c_int;
                while j < 16 as ::core::ffi::c_int {
                    if k & (1 as ::core::ffi::c_int) << 15 as ::core::ffi::c_int - j != 0 {
                        let c2rust_fresh2 = symTotal;
                        symTotal = symTotal + 1;
                        *symToByte.offset(c2rust_fresh2 as isize) =
                            (16 as ::core::ffi::c_int * i + j) as ::core::ffi::c_uchar;
                    }
                    j += 1;
                }
            }
            i += 1;
        }
        groupCount = get_bits(bd, 3 as ::core::ffi::c_char) as ::core::ffi::c_int;
        if groupCount < 2 as ::core::ffi::c_int || groupCount > MAX_GROUPS {
            return RETVAL_DATA_ERROR;
        }
        nSelectors = get_bits(bd, 15 as ::core::ffi::c_char) as ::core::ffi::c_int;
        if nSelectors == 0 {
            return RETVAL_DATA_ERROR;
        }
        i = 0 as ::core::ffi::c_int;
        while i < groupCount {
            *mtfSymbol.offset(i as isize) = i as ::core::ffi::c_uchar;
            i += 1;
        }
        i = 0 as ::core::ffi::c_int;
        while i < nSelectors {
            j = 0 as ::core::ffi::c_int;
            while get_bits(bd, 1 as ::core::ffi::c_char) != 0 {
                if j >= groupCount {
                    return RETVAL_DATA_ERROR;
                }
                j += 1;
            }
            uc = *mtfSymbol.offset(j as isize);
            while j != 0 {
                *mtfSymbol.offset(j as isize) =
                    *mtfSymbol.offset((j - 1 as ::core::ffi::c_int) as isize);
                j -= 1;
            }
            *selectors.offset(i as isize) = uc;
            *mtfSymbol.offset(0 as ::core::ffi::c_int as isize) = *selectors.offset(i as isize);
            i += 1;
        }
        symCount = symTotal + 2 as ::core::ffi::c_int;
        j = 0 as ::core::ffi::c_int;
        while j < groupCount {
            let mut length: [::core::ffi::c_uchar; 258] = [0; 258];
            let mut temp: [::core::ffi::c_ushort; 21] = [0; 21];
            let mut minLen: ::core::ffi::c_int = 0;
            let mut maxLen: ::core::ffi::c_int = 0;
            let mut pp: ::core::ffi::c_int = 0;
            t = get_bits(bd, 5 as ::core::ffi::c_char).wrapping_sub(1 as ::core::ffi::c_uint)
                as ::core::ffi::c_int;
            i = 0 as ::core::ffi::c_int;
            while i < symCount {
                loop {
                    if t as ::core::ffi::c_uint
                        > (MAX_HUFCODE_BITS - 1 as ::core::ffi::c_int) as ::core::ffi::c_uint
                    {
                        return RETVAL_DATA_ERROR;
                    }
                    k = get_bits(bd, 2 as ::core::ffi::c_char) as ::core::ffi::c_int;
                    if k < 2 as ::core::ffi::c_int {
                        (*bd).inbufBitCount = (*bd).inbufBitCount.wrapping_add(1);
                        break;
                    } else {
                        t += (k + 1 as ::core::ffi::c_int & 2 as ::core::ffi::c_int)
                            - 1 as ::core::ffi::c_int;
                    }
                }
                length[i as usize] = (t + 1 as ::core::ffi::c_int) as ::core::ffi::c_uchar;
                i += 1;
            }
            maxLen = length[0 as ::core::ffi::c_int as usize] as ::core::ffi::c_int;
            minLen = maxLen;
            i = 1 as ::core::ffi::c_int;
            while i < symCount {
                if length[i as usize] as ::core::ffi::c_int > maxLen {
                    maxLen = length[i as usize] as ::core::ffi::c_int;
                } else if (length[i as usize] as ::core::ffi::c_int) < minLen {
                    minLen = length[i as usize] as ::core::ffi::c_int;
                }
                i += 1;
            }
            hufGroup = (&raw mut (*bd).groups as *mut group_data).offset(j as isize);
            (*hufGroup).minLen = minLen;
            (*hufGroup).maxLen = maxLen;
            base = (&raw mut (*hufGroup).base as *mut ::core::ffi::c_int)
                .offset(-(1 as ::core::ffi::c_int as isize));
            limit = (&raw mut (*hufGroup).limit as *mut ::core::ffi::c_int)
                .offset(-(1 as ::core::ffi::c_int as isize));
            pp = 0 as ::core::ffi::c_int;
            i = minLen;
            while i <= maxLen {
                *limit.offset(i as isize) = 0 as ::core::ffi::c_int;
                temp[i as usize] = *limit.offset(i as isize) as ::core::ffi::c_ushort;
                t = 0 as ::core::ffi::c_int;
                while t < symCount {
                    if length[t as usize] as ::core::ffi::c_int == i {
                        let c2rust_fresh3 = pp;
                        pp = pp + 1;
                        (*hufGroup).permute[c2rust_fresh3 as usize] = t;
                    }
                    t += 1;
                }
                i += 1;
            }
            i = 0 as ::core::ffi::c_int;
            while i < symCount {
                temp[length[i as usize] as usize] =
                    temp[length[i as usize] as usize].wrapping_add(1);
                i += 1;
            }
            t = 0 as ::core::ffi::c_int;
            pp = t;
            i = minLen;
            while i < maxLen {
                pp += temp[i as usize] as ::core::ffi::c_int;
                *limit.offset(i as isize) = (pp << maxLen - i) - 1 as ::core::ffi::c_int;
                pp <<= 1 as ::core::ffi::c_int;
                t += temp[i as usize] as ::core::ffi::c_int;
                *base.offset((i + 1 as ::core::ffi::c_int) as isize) = pp - t;
                i += 1;
            }
            *limit.offset((maxLen + 1 as ::core::ffi::c_int) as isize) = INT_MAX;
            *limit.offset(maxLen as isize) =
                pp + temp[maxLen as usize] as ::core::ffi::c_int - 1 as ::core::ffi::c_int;
            *base.offset(minLen as isize) = 0 as ::core::ffi::c_int;
            j += 1;
        }
        i = 0 as ::core::ffi::c_int;
        while i < 256 as ::core::ffi::c_int {
            *byteCount.offset(i as isize) = 0 as ::core::ffi::c_int;
            *mtfSymbol.offset(i as isize) = i as ::core::ffi::c_uchar;
            i += 1;
        }
        selector = 0 as ::core::ffi::c_int;
        symCount = selector;
        dbufCount = symCount;
        runPos = dbufCount;
        loop {
            let c2rust_fresh4 = symCount;
            symCount = symCount - 1;
            if c2rust_fresh4 == 0 {
                symCount = GROUP_SIZE - 1 as ::core::ffi::c_int;
                if selector >= nSelectors {
                    return RETVAL_DATA_ERROR;
                }
                let c2rust_fresh5 = selector;
                selector = selector + 1;
                hufGroup = (&raw mut (*bd).groups as *mut group_data).offset(
                    *selectors.offset(c2rust_fresh5 as isize) as ::core::ffi::c_int as isize,
                );
                base = (&raw mut (*hufGroup).base as *mut ::core::ffi::c_int)
                    .offset(-(1 as ::core::ffi::c_int as isize));
                limit = (&raw mut (*hufGroup).limit as *mut ::core::ffi::c_int)
                    .offset(-(1 as ::core::ffi::c_int as isize));
            }
            '_got_huff_bits: {
                while (*bd).inbufBitCount < (*hufGroup).maxLen as ::core::ffi::c_uint {
                    if (*bd).inbufPos == (*bd).inbufCount {
                        j = get_bits(bd, (*hufGroup).maxLen as ::core::ffi::c_char)
                            as ::core::ffi::c_int;
                        break '_got_huff_bits;
                    } else {
                        let c2rust_fresh6 = (*bd).inbufPos;
                        (*bd).inbufPos = (*bd).inbufPos + 1;
                        (*bd).inbufBits = (*bd).inbufBits << 8 as ::core::ffi::c_int
                            | *(*bd).inbuf.offset(c2rust_fresh6 as isize) as ::core::ffi::c_uint;
                        (*bd).inbufBitCount =
                            (*bd).inbufBitCount.wrapping_add(8 as ::core::ffi::c_uint);
                    }
                }
                (*bd).inbufBitCount = (*bd)
                    .inbufBitCount
                    .wrapping_sub((*hufGroup).maxLen as ::core::ffi::c_uint);
                j = ((*bd).inbufBits >> (*bd).inbufBitCount
                    & (((1 as ::core::ffi::c_int) << (*hufGroup).maxLen) - 1 as ::core::ffi::c_int)
                        as ::core::ffi::c_uint) as ::core::ffi::c_int;
            }
            i = (*hufGroup).minLen;
            while j > *limit.offset(i as isize) {
                i += 1;
            }
            (*bd).inbufBitCount = (*bd)
                .inbufBitCount
                .wrapping_add(((*hufGroup).maxLen - i) as ::core::ffi::c_uint);
            if i > (*hufGroup).maxLen || {
                j = (j >> (*hufGroup).maxLen - i) - *base.offset(i as isize);
                j as ::core::ffi::c_uint >= MAX_SYMBOLS as ::core::ffi::c_uint
            } {
                return RETVAL_DATA_ERROR;
            }
            nextSym = (*hufGroup).permute[j as usize];
            if nextSym as ::core::ffi::c_uint <= SYMBOL_RUNB as ::core::ffi::c_uint {
                if runPos == 0 {
                    runPos = 1 as ::core::ffi::c_int;
                    t = 0 as ::core::ffi::c_int;
                }
                t += runPos << nextSym;
                runPos <<= 1 as ::core::ffi::c_int;
            } else {
                if runPos != 0 {
                    runPos = 0 as ::core::ffi::c_int;
                    if dbufCount + t >= dbufSize {
                        return RETVAL_DATA_ERROR;
                    }
                    uc = *symToByte
                        .offset(*mtfSymbol.offset(0 as ::core::ffi::c_int as isize) as isize);
                    *byteCount.offset(uc as isize) += t;
                    loop {
                        let c2rust_fresh7 = t;
                        t = t - 1;
                        if c2rust_fresh7 == 0 {
                            break;
                        }
                        let c2rust_fresh8 = dbufCount;
                        dbufCount = dbufCount + 1;
                        *dbuf.offset(c2rust_fresh8 as isize) = uc as ::core::ffi::c_uint;
                    }
                }
                if nextSym > symTotal {
                    break;
                }
                if dbufCount >= dbufSize {
                    return RETVAL_DATA_ERROR;
                }
                i = nextSym - 1 as ::core::ffi::c_int;
                uc = *mtfSymbol.offset(i as isize);
                loop {
                    *mtfSymbol.offset(i as isize) =
                        *mtfSymbol.offset((i - 1 as ::core::ffi::c_int) as isize);
                    i -= 1;
                    if i == 0 {
                        break;
                    }
                }
                *mtfSymbol.offset(0 as ::core::ffi::c_int as isize) = uc;
                uc = *symToByte.offset(uc as isize);
                *byteCount.offset(uc as isize) += 1;
                let c2rust_fresh9 = dbufCount;
                dbufCount = dbufCount + 1;
                *dbuf.offset(c2rust_fresh9 as isize) = uc as ::core::ffi::c_uint;
            }
        }
        j = 0 as ::core::ffi::c_int;
        i = 0 as ::core::ffi::c_int;
        while i < 256 as ::core::ffi::c_int {
            k = j + *byteCount.offset(i as isize);
            *byteCount.offset(i as isize) = j;
            j = k;
            i += 1;
        }
        i = 0 as ::core::ffi::c_int;
        while i < dbufCount {
            uc = (*dbuf.offset(i as isize) & 0xff as ::core::ffi::c_uint) as ::core::ffi::c_uchar;
            *dbuf.offset(*byteCount.offset(uc as isize) as isize) |=
                (i << 8 as ::core::ffi::c_int) as ::core::ffi::c_uint;
            *byteCount.offset(uc as isize) += 1;
            i += 1;
        }
        if dbufCount != 0 {
            if origPtr >= dbufCount as ::core::ffi::c_uint {
                return RETVAL_DATA_ERROR;
            }
            (*bd).writePos = *dbuf.offset(origPtr as isize) as ::core::ffi::c_int;
            (*bd).writeCurrent = ((*bd).writePos & 0xff as ::core::ffi::c_int)
                as ::core::ffi::c_uchar as ::core::ffi::c_int;
            (*bd).writePos >>= 8 as ::core::ffi::c_int;
            (*bd).writeRunCountdown = 5 as ::core::ffi::c_int;
        }
        (*bd).writeCount = dbufCount;
        return RETVAL_OK;
    }
}
#[link_section = ".init.text"]
#[cold]
unsafe extern "C" fn read_bunzip(
    mut bd: *mut bunzip_data,
    mut outbuf: *mut ::core::ffi::c_char,
    mut len: ::core::ffi::c_int,
) -> ::core::ffi::c_int {
    unsafe {
        let mut c2rust_current_block: u64;
        let mut dbuf: *const ::core::ffi::c_uint = ::core::ptr::null::<::core::ffi::c_uint>();
        let mut pos: ::core::ffi::c_int = 0;
        let mut xcurrent: ::core::ffi::c_int = 0;
        let mut previous: ::core::ffi::c_int = 0;
        let mut gotcount: ::core::ffi::c_int = 0;
        if (*bd).writeCount < 0 as ::core::ffi::c_int {
            return (*bd).writeCount;
        }
        gotcount = 0 as ::core::ffi::c_int;
        dbuf = (*bd).dbuf;
        pos = (*bd).writePos;
        xcurrent = (*bd).writeCurrent;
        if (*bd).writeCopies != 0 {
            (*bd).writeCopies -= 1;
            c2rust_current_block = 4906268039856690917;
        } else {
            c2rust_current_block = 17788412896529399552;
        }
        loop {
            match c2rust_current_block {
                17788412896529399552 => {
                    previous = get_next_block(bd);
                    if previous != 0 {
                        (*bd).writeCount = previous;
                        return if previous != RETVAL_LAST_BLOCK {
                            previous
                        } else {
                            gotcount
                        };
                    }
                    (*bd).writeCRC = 0xffffffff as ::core::ffi::c_ulong as ::core::ffi::c_uint;
                    pos = (*bd).writePos;
                    xcurrent = (*bd).writeCurrent;
                }
                _ => {
                    if gotcount >= len {
                        (*bd).writePos = pos;
                        (*bd).writeCurrent = xcurrent;
                        (*bd).writeCopies += 1;
                        return len;
                    }
                    let c2rust_fresh0 = gotcount;
                    gotcount = gotcount + 1;
                    *outbuf.offset(c2rust_fresh0 as isize) = xcurrent as ::core::ffi::c_char;
                    (*bd).writeCRC = (*bd).writeCRC << 8 as ::core::ffi::c_int
                        ^ (*bd).crc32Table[((*bd).writeCRC >> 24 as ::core::ffi::c_int
                            ^ xcurrent as ::core::ffi::c_uint)
                            as usize];
                    if (*bd).writeCopies != 0 {
                        (*bd).writeCopies -= 1;
                        c2rust_current_block = 4906268039856690917;
                        continue;
                    }
                }
            }
            loop {
                let c2rust_fresh1 = (*bd).writeCount;
                (*bd).writeCount = (*bd).writeCount - 1;
                if c2rust_fresh1 == 0 {
                    (*bd).writeCRC = !(*bd).writeCRC;
                    (*bd).totalCRC = ((*bd).totalCRC << 1 as ::core::ffi::c_int
                        | (*bd).totalCRC >> 31 as ::core::ffi::c_int)
                        ^ (*bd).writeCRC;
                    if (*bd).writeCRC != (*bd).headerCRC {
                        (*bd).totalCRC = (*bd).headerCRC.wrapping_add(1 as ::core::ffi::c_uint);
                        return RETVAL_LAST_BLOCK;
                    }
                    c2rust_current_block = 17788412896529399552;
                    break;
                } else {
                    previous = xcurrent;
                    pos = *dbuf.offset(pos as isize) as ::core::ffi::c_int;
                    xcurrent = pos & 0xff as ::core::ffi::c_int;
                    pos >>= 8 as ::core::ffi::c_int;
                    (*bd).writeRunCountdown -= 1;
                    if (*bd).writeRunCountdown != 0 {
                        if xcurrent != previous {
                            (*bd).writeRunCountdown = 4 as ::core::ffi::c_int;
                        }
                        c2rust_current_block = 4906268039856690917;
                        break;
                    } else {
                        (*bd).writeCopies = xcurrent;
                        xcurrent = previous;
                        (*bd).writeRunCountdown = 5 as ::core::ffi::c_int;
                        if (*bd).writeCopies == 0 {
                            continue;
                        }
                        (*bd).writeCopies -= 1;
                        c2rust_current_block = 4906268039856690917;
                        break;
                    }
                }
            }
        }
    }
}
#[link_section = ".init.text"]
#[cold]
unsafe extern "C" fn nofill(
    mut buf: *mut ::core::ffi::c_void,
    mut len: ::core::ffi::c_ulong,
) -> ::core::ffi::c_long {
    unsafe {
        return -1 as ::core::ffi::c_long;
    }
}
#[link_section = ".init.text"]
#[cold]
unsafe extern "C" fn start_bunzip(
    mut bdp: *mut *mut bunzip_data,
    mut inbuf: *mut ::core::ffi::c_void,
    mut len: ::core::ffi::c_long,
    mut fill: Option<
        unsafe extern "C" fn(*mut ::core::ffi::c_void, ::core::ffi::c_ulong) -> ::core::ffi::c_long,
    >,
) -> ::core::ffi::c_int {
    unsafe {
        let mut bd: *mut bunzip_data = ::core::ptr::null_mut::<bunzip_data>();
        let mut i: ::core::ffi::c_uint = 0;
        let mut j: ::core::ffi::c_uint = 0;
        let mut c: ::core::ffi::c_uint = 0;
        let BZh0: ::core::ffi::c_uint = (('B' as ::core::ffi::c_int as ::core::ffi::c_uint)
            << 24 as ::core::ffi::c_int)
            .wrapping_add(
                ('Z' as ::core::ffi::c_int as ::core::ffi::c_uint) << 16 as ::core::ffi::c_int,
            )
            .wrapping_add(
                ('h' as ::core::ffi::c_int as ::core::ffi::c_uint) << 8 as ::core::ffi::c_int,
            )
            .wrapping_add('0' as ::core::ffi::c_int as ::core::ffi::c_uint);
        i = ::core::mem::size_of::<bunzip_data>() as ::core::ffi::c_uint;
        *bdp = ({
            ({
                let mut _res: *mut ::core::ffi::c_void =
                    ::core::ptr::null_mut::<::core::ffi::c_void>();
                if mem_alloc_profiling_enabled() {
                    let mut _old: *mut alloc_tag = ::core::ptr::null_mut::<alloc_tag>();
                    _old = ::core::ptr::null_mut::<alloc_tag>();
                    _res = _kmalloc_noprof(
                        i as size_t,
                        ((1 as ::core::ffi::c_ulong)
                            << ___GFP_DIRECT_RECLAIM_BIT as ::core::ffi::c_int
                            | (1 as ::core::ffi::c_ulong)
                                << ___GFP_KSWAPD_RECLAIM_BIT as ::core::ffi::c_int)
                            as gfp_t
                            | ((1 as ::core::ffi::c_ulong) << ___GFP_IO_BIT as ::core::ffi::c_int)
                                as gfp_t
                            | ((1 as ::core::ffi::c_ulong) << ___GFP_FS_BIT as ::core::ffi::c_int)
                                as gfp_t,
                        kmalloc_token_t {},
                    ) as *mut ::core::ffi::c_void;
                } else {
                    _res = _kmalloc_noprof(
                        i as size_t,
                        ((1 as ::core::ffi::c_ulong)
                            << ___GFP_DIRECT_RECLAIM_BIT as ::core::ffi::c_int
                            | (1 as ::core::ffi::c_ulong)
                                << ___GFP_KSWAPD_RECLAIM_BIT as ::core::ffi::c_int)
                            as gfp_t
                            | ((1 as ::core::ffi::c_ulong) << ___GFP_IO_BIT as ::core::ffi::c_int)
                                as gfp_t
                            | ((1 as ::core::ffi::c_ulong) << ___GFP_FS_BIT as ::core::ffi::c_int)
                                as gfp_t,
                        kmalloc_token_t {},
                    ) as *mut ::core::ffi::c_void;
                }
                _res
            })
        }) as *mut bunzip_data;
        bd = *bdp;
        if bd.is_null() {
            return RETVAL_OUT_OF_MEMORY;
        }
        memset(
            bd as *mut ::core::ffi::c_void,
            0 as ::core::ffi::c_int,
            ::core::mem::size_of::<bunzip_data>() as size_t,
        );
        (*bd).inbuf = inbuf as *mut ::core::ffi::c_uchar;
        (*bd).inbufCount = len;
        if fill.is_some() {
            (*bd).fill = fill;
        } else {
            (*bd).fill = Some(
                nofill
                    as unsafe extern "C" fn(
                        *mut ::core::ffi::c_void,
                        ::core::ffi::c_ulong,
                    ) -> ::core::ffi::c_long,
            )
                as Option<
                    unsafe extern "C" fn(
                        *mut ::core::ffi::c_void,
                        ::core::ffi::c_ulong,
                    ) -> ::core::ffi::c_long,
                >;
        }
        i = 0 as ::core::ffi::c_uint;
        while i < 256 as ::core::ffi::c_uint {
            c = i << 24 as ::core::ffi::c_int;
            j = 8 as ::core::ffi::c_uint;
            while j != 0 {
                c = if c & 0x80000000 as ::core::ffi::c_uint != 0 {
                    c << 1 as ::core::ffi::c_int ^ CRC32_POLY_BE as ::core::ffi::c_uint
                } else {
                    c << 1 as ::core::ffi::c_int
                };
                j = j.wrapping_sub(1);
            }
            (*bd).crc32Table[i as usize] = c;
            i = i.wrapping_add(1);
        }
        i = get_bits(bd, 32 as ::core::ffi::c_char);
        if i.wrapping_sub(BZh0).wrapping_sub(1 as ::core::ffi::c_uint) >= 9 as ::core::ffi::c_uint {
            return RETVAL_NOT_BZIP_DATA;
        }
        (*bd).dbufSize = (100000 as ::core::ffi::c_int as ::core::ffi::c_uint)
            .wrapping_mul(i.wrapping_sub(BZh0));
        (*bd).dbuf = ({
            ({
                let mut _res: *mut ::core::ffi::c_void =
                    ::core::ptr::null_mut::<::core::ffi::c_void>();
                if mem_alloc_profiling_enabled() {
                    let mut _old: *mut alloc_tag = ::core::ptr::null_mut::<alloc_tag>();
                    _old = ::core::ptr::null_mut::<alloc_tag>();
                    _res = vmalloc_noprof(((*bd).dbufSize as ::core::ffi::c_ulong).wrapping_mul(
                        ::core::mem::size_of::<::core::ffi::c_int>() as ::core::ffi::c_ulong,
                    )) as *mut ::core::ffi::c_void;
                } else {
                    _res = vmalloc_noprof(((*bd).dbufSize as ::core::ffi::c_ulong).wrapping_mul(
                        ::core::mem::size_of::<::core::ffi::c_int>() as ::core::ffi::c_ulong,
                    )) as *mut ::core::ffi::c_void;
                }
                _res
            })
        }) as *mut ::core::ffi::c_uint;
        if (*bd).dbuf.is_null() {
            return RETVAL_OUT_OF_MEMORY;
        }
        return RETVAL_OK;
    }
}
#[no_mangle]
#[link_section = ".init.text"]
#[cold]
pub unsafe extern "C" fn bunzip2(
    mut buf: *mut ::core::ffi::c_uchar,
    mut len: ::core::ffi::c_long,
    mut fill: Option<
        unsafe extern "C" fn(*mut ::core::ffi::c_void, ::core::ffi::c_ulong) -> ::core::ffi::c_long,
    >,
    mut flush: Option<
        unsafe extern "C" fn(*mut ::core::ffi::c_void, ::core::ffi::c_ulong) -> ::core::ffi::c_long,
    >,
    mut outbuf: *mut ::core::ffi::c_uchar,
    mut pos: *mut ::core::ffi::c_long,
    mut error: Option<unsafe extern "C" fn(*mut ::core::ffi::c_char) -> ()>,
) -> ::core::ffi::c_int {
    unsafe {
        let mut bd: *mut bunzip_data = ::core::ptr::null_mut::<bunzip_data>();
        let mut i: ::core::ffi::c_int = -1 as ::core::ffi::c_int;
        let mut inbuf: *mut ::core::ffi::c_uchar = ::core::ptr::null_mut::<::core::ffi::c_uchar>();
        if flush.is_some() {
            outbuf = ({
                ({
                    let mut _res: *mut ::core::ffi::c_void =
                        ::core::ptr::null_mut::<::core::ffi::c_void>();
                    if mem_alloc_profiling_enabled() {
                        let mut _old: *mut alloc_tag = ::core::ptr::null_mut::<alloc_tag>();
                        _old = ::core::ptr::null_mut::<alloc_tag>();
                        _res = _kmalloc_noprof(
                            4096 as size_t,
                            ((1 as ::core::ffi::c_ulong)
                                << ___GFP_DIRECT_RECLAIM_BIT as ::core::ffi::c_int
                                | (1 as ::core::ffi::c_ulong)
                                    << ___GFP_KSWAPD_RECLAIM_BIT as ::core::ffi::c_int)
                                as gfp_t
                                | ((1 as ::core::ffi::c_ulong)
                                    << ___GFP_IO_BIT as ::core::ffi::c_int)
                                    as gfp_t
                                | ((1 as ::core::ffi::c_ulong)
                                    << ___GFP_FS_BIT as ::core::ffi::c_int)
                                    as gfp_t,
                            kmalloc_token_t {},
                        ) as *mut ::core::ffi::c_void;
                    } else {
                        _res = _kmalloc_noprof(
                            4096 as size_t,
                            ((1 as ::core::ffi::c_ulong)
                                << ___GFP_DIRECT_RECLAIM_BIT as ::core::ffi::c_int
                                | (1 as ::core::ffi::c_ulong)
                                    << ___GFP_KSWAPD_RECLAIM_BIT as ::core::ffi::c_int)
                                as gfp_t
                                | ((1 as ::core::ffi::c_ulong)
                                    << ___GFP_IO_BIT as ::core::ffi::c_int)
                                    as gfp_t
                                | ((1 as ::core::ffi::c_ulong)
                                    << ___GFP_FS_BIT as ::core::ffi::c_int)
                                    as gfp_t,
                            kmalloc_token_t {},
                        ) as *mut ::core::ffi::c_void;
                    }
                    _res
                })
            }) as *mut ::core::ffi::c_uchar;
        }
        if outbuf.is_null() {
            error.expect("non-null function pointer")(
                b"Could not allocate output buffer\0".as_ptr() as *const ::core::ffi::c_char
                    as *mut ::core::ffi::c_char,
            );
            return RETVAL_OUT_OF_MEMORY;
        }
        if !buf.is_null() {
            inbuf = buf;
        } else {
            inbuf = ({
                ({
                    let mut _res: *mut ::core::ffi::c_void =
                        ::core::ptr::null_mut::<::core::ffi::c_void>();
                    if mem_alloc_profiling_enabled() {
                        let mut _old: *mut alloc_tag = ::core::ptr::null_mut::<alloc_tag>();
                        _old = ::core::ptr::null_mut::<alloc_tag>();
                        _res = _kmalloc_noprof(
                            4096 as size_t,
                            ((1 as ::core::ffi::c_ulong)
                                << ___GFP_DIRECT_RECLAIM_BIT as ::core::ffi::c_int
                                | (1 as ::core::ffi::c_ulong)
                                    << ___GFP_KSWAPD_RECLAIM_BIT as ::core::ffi::c_int)
                                as gfp_t
                                | ((1 as ::core::ffi::c_ulong)
                                    << ___GFP_IO_BIT as ::core::ffi::c_int)
                                    as gfp_t
                                | ((1 as ::core::ffi::c_ulong)
                                    << ___GFP_FS_BIT as ::core::ffi::c_int)
                                    as gfp_t,
                            kmalloc_token_t {},
                        ) as *mut ::core::ffi::c_void;
                    } else {
                        _res = _kmalloc_noprof(
                            4096 as size_t,
                            ((1 as ::core::ffi::c_ulong)
                                << ___GFP_DIRECT_RECLAIM_BIT as ::core::ffi::c_int
                                | (1 as ::core::ffi::c_ulong)
                                    << ___GFP_KSWAPD_RECLAIM_BIT as ::core::ffi::c_int)
                                as gfp_t
                                | ((1 as ::core::ffi::c_ulong)
                                    << ___GFP_IO_BIT as ::core::ffi::c_int)
                                    as gfp_t
                                | ((1 as ::core::ffi::c_ulong)
                                    << ___GFP_FS_BIT as ::core::ffi::c_int)
                                    as gfp_t,
                            kmalloc_token_t {},
                        ) as *mut ::core::ffi::c_void;
                    }
                    _res
                })
            }) as *mut ::core::ffi::c_uchar;
        }
        if inbuf.is_null() {
            error.expect("non-null function pointer")(
                b"Could not allocate input buffer\0".as_ptr() as *const ::core::ffi::c_char
                    as *mut ::core::ffi::c_char,
            );
            i = RETVAL_OUT_OF_MEMORY;
        } else {
            i = start_bunzip(&raw mut bd, inbuf as *mut ::core::ffi::c_void, len, fill);
            if i == 0 {
                loop {
                    i = read_bunzip(bd, outbuf as *mut ::core::ffi::c_char, BZIP2_IOBUF_SIZE);
                    if i <= 0 as ::core::ffi::c_int {
                        break;
                    }
                    if flush.is_none() {
                        outbuf = outbuf.offset(i as isize);
                    } else {
                        if i as ::core::ffi::c_long
                            == flush.expect("non-null function pointer")(
                                outbuf as *mut ::core::ffi::c_void,
                                i as ::core::ffi::c_ulong,
                            )
                        {
                            continue;
                        }
                        i = RETVAL_UNEXPECTED_OUTPUT_EOF;
                        break;
                    }
                }
            }
            if i == RETVAL_LAST_BLOCK {
                if (*bd).headerCRC != (*bd).totalCRC {
                    error.expect("non-null function pointer")(
                        b"Data integrity error when decompressing.\0".as_ptr()
                            as *const ::core::ffi::c_char
                            as *mut ::core::ffi::c_char,
                    );
                } else {
                    i = RETVAL_OK;
                }
            } else if i == RETVAL_UNEXPECTED_OUTPUT_EOF {
                error.expect("non-null function pointer")(
                    b"Compressed file ends unexpectedly\0".as_ptr() as *const ::core::ffi::c_char
                        as *mut ::core::ffi::c_char,
                );
            }
            if !bd.is_null() {
                if !(*bd).dbuf.is_null() {
                    vfree((*bd).dbuf as *const ::core::ffi::c_void);
                }
                if !pos.is_null() {
                    *pos = (*bd).inbufPos;
                }
                kfree(bd as *const ::core::ffi::c_void);
            }
            if buf.is_null() {
                kfree(inbuf as *const ::core::ffi::c_void);
            }
        }
        if flush.is_some() {
            kfree(outbuf as *const ::core::ffi::c_void);
        }
        return i;
    }
}
pub const CONFIG_PAGE_SHIFT: ::core::ffi::c_int = 12 as ::core::ffi::c_int;

/// Functional KUnit coverage for `bunzip2()` — calls the real translated
/// decompression entry point on a host-`bzip2`-compressed blob and checks
/// the output matches the known plaintext byte-for-byte. Closes the
/// "links and boots but is never runtime-exercised" gap noted for this
/// file in `docs/combined-boot-attempt-2026-07-18.md`: this kernel's
/// initramfs is gzip-compressed, so `bunzip2()` is otherwise never called
/// during boot, and no other KUnit suite in this build exercises bzip2
/// decode.
///
/// Test data: `lib/testdata/bunzip2_kunit_test.bz2`, produced by the real
/// `bzip2 -9` CLI (not hand-rolled) from a short known plaintext, embedded
/// via `include_bytes!` at compile time.
///
/// This suite is registered by hand rather than via `#[kunit_tests(...)]`.
/// `bunzip2()` is `__init` (`.init.text`) in this non-`STATIC`/non-`PREBOOT`
/// build (see `include/linux/decompress/mm.h`'s `#define INIT __init` when
/// `STATIC` is undefined), so the test function that calls it must live in
/// `.init.text` too, and so must the wrapper that calls the test function —
/// but `#[kunit_tests(...)]`'s generated wrapper (`kunit_rust_wrapper_*`,
/// via `kernel::kunit_unsafe_test_suite!`) is placed in `.text.unlikely.`
/// unconditionally, causing a genuine modpost section-mismatch error no
/// matter how the leaf test fn itself is annotated. The C-kernel idiom for
/// an init-calling KUnit suite (`lib/kunit/kunit-example-test.c`'s
/// `example_init_test`, also used in this tree by
/// `init/initramfs_test.c:kunit_test_init_section_suites`) is: mark the
/// test function `__init`, and register the suite via
/// `kunit_test_init_section_suites()` instead of `kunit_test_suites()` —
/// that macro places the suite-pointer array in `.kunit_init_test_suites`
/// (a section KUnit also scans, just during the init window) with a
/// `_probe`-suffixed symbol name, which is modpost's whitelist heuristic
/// for "this data legitimately references init code". This module
/// hand-expands that same shape in Rust: everything reachable from the
/// test case (`decompresses_known_payload`, `test_error_cb`, their
/// C-ABI wrapper) is `#[link_section = ".init.text"]`, and the suite/case
/// arrays are placed in `.kunit_init_test_suites`/`_probe`-suffixed to
/// match.
#[cfg(CONFIG_KUNIT = "y")]
mod tests {
    use super::*;

    const PLAINTEXT: &[u8] =
        b"linux-rs KUnit bunzip2 test payload: the quick brown fox jumps over the lazy dog 0123456789.\n";
    const COMPRESSED: &[u8] = include_bytes!("testdata/bunzip2_kunit_test.bz2");

    /// `bunzip2()`'s `error` callback — the C original's signature is
    /// `void (*)(char *x)`, unconditionally called (never null-checked) on
    /// any error path, so a real callback must be supplied even though this
    /// test expects the happy path. Panicking here turns a decode failure
    /// into an immediate, informative KUnit test failure rather than a
    /// silent bad `pos`/return code.
    #[link_section = ".init.text"]
    unsafe extern "C" fn test_error_cb(msg: *mut ::core::ffi::c_char) {
        // SAFETY: `bunzip2()` always passes a valid NUL-terminated C string
        // literal (e.g. "Data error" style constants) to `error`.
        let msg = unsafe { core::ffi::CStr::from_ptr(msg) };
        panic!("bunzip2() reported error: {:?}", msg);
    }

    /// Decompresses the embedded real-bzip2-compressed blob via the actual
    /// translated `bunzip2()` entry point (`buf`/`len` = full compressed
    /// stream, `fill`/`flush` = None so the whole input/output buffers are
    /// used directly, matching how `lib/decompress.c`'s dispatcher calls it
    /// for a `<len>`-known in-memory compressed buffer) and checks the
    /// decompressed bytes match the known original plaintext exactly.
    #[link_section = ".init.text"]
    fn decompresses_known_payload() {
        // `bunzip2()` with `flush == None` calls `read_bunzip(bd, outbuf,
        // BZIP2_IOBUF_SIZE)` with the hardcoded 4096-byte chunk size
        // regardless of the caller's actual buffer size (mirrors the C
        // original's `outbuf = malloc(BZIP2_IOBUF_SIZE)` non-`flush` path
        // in `lib/decompress_bunzip2.c`) — `outbuf` must be at least that
        // large to avoid an out-of-bounds write.
        let mut outbuf = [0u8; 4096];
        let mut pos: ::core::ffi::c_long = 0;

        // SAFETY: `COMPRESSED` is a valid, live, immutable byte slice for
        // the duration of the call; `bunzip2` only reads through `buf` (no
        // `fill` callback supplied, so it never treats `buf` as a streaming
        // handle) and writes through `outbuf`, which is large enough for
        // the known (93-byte) plaintext. `error` is a real callback, not
        // null, matching the always-called-on-error contract.
        let ret = unsafe {
            bunzip2(
                COMPRESSED.as_ptr() as *mut ::core::ffi::c_uchar,
                COMPRESSED.len() as ::core::ffi::c_long,
                None,
                None,
                outbuf.as_mut_ptr(),
                &mut pos as *mut ::core::ffi::c_long,
                Some(test_error_cb),
            )
        };

        // ret == 0 is RETVAL_OK.
        kernel::kunit_assert_eq!(
            "decompresses_known_payload",
            c"lib/decompress_bunzip2_rs.rs",
            0,
            ret,
            0
        );
        kernel::kunit_assert!(
            "decompresses_known_payload",
            c"lib/decompress_bunzip2_rs.rs",
            0,
            &outbuf[..PLAINTEXT.len()] == PLAINTEXT
        );
    }

    #[link_section = ".init.text"]
    unsafe extern "C" fn kunit_rust_wrapper_decompresses_known_payload(
        _test: *mut ::kernel::bindings::kunit,
    ) {
        decompresses_known_payload();
    }

    // NB: these are *data*, not code — they belong in `.init.data`
    // (freed alongside `INIT_DATA`, which is what `KUNIT_INIT_TABLE()` /
    // `.kunit_init_test_suites` is embedded inside per
    // `include/asm-generic/vmlinux.lds.h`), not `.init.text` (freed
    // alongside `INIT_TEXT`, a distinct linker output section covering
    // only actual code). Putting the `kunit_suite` struct itself in
    // `.init.text` by mistake here (an earlier iteration of this test)
    // produced a real boot-time oops: `kunit_run_all_tests()`'s
    // `__kunit_init_suites_start`/`_end` bounds walk correctly found the
    // pointer in `.kunit_init_test_suites`, but the pointee lived in the
    // wrong init section relative to when each is unmapped, so by the
    // time it was dereferenced the page was gone
    // (`Unable to handle kernel paging request`). Only the two functions
    // above that are genuinely executed code (the wrapper and the test
    // body, both reachable from `bunzip2()`, itself real `.init.text`)
    // stay in `.init.text`.
    #[link_section = ".init.data"]
    static mut TEST_CASES: [::kernel::bindings::kunit_case; 2] = [
        ::kernel::kunit::kunit_case(
            kernel::c_str!("decompresses_known_payload"),
            kunit_rust_wrapper_decompresses_known_payload,
        ),
        ::pin_init::zeroed(),
    ];

    #[link_section = ".init.data"]
    static SUITE_NAME: [::kernel::ffi::c_char; 256] = {
        let name_u8 = "rust_decompress_bunzip2".as_bytes();
        let mut ret = [0; 256];
        let mut i = 0;
        while i < name_u8.len() {
            ret[i] = name_u8[i] as ::kernel::ffi::c_char;
            i += 1;
        }
        ret
    };

    #[link_section = ".init.data"]
    static mut KUNIT_SUITE_rust_decompress_bunzip2_probe: ::kernel::bindings::kunit_suite =
        ::kernel::bindings::kunit_suite {
            name: SUITE_NAME,
            // SAFETY: `TEST_CASES` is `static`, valid for the suite's
            // lifetime.
            test_cases: unsafe {
                ::core::ptr::addr_of_mut!(TEST_CASES).cast::<::kernel::bindings::kunit_case>()
            },
            suite_init: None,
            suite_exit: None,
            init: None,
            exit: None,
            attr: ::kernel::bindings::kunit_attributes {
                speed: ::kernel::bindings::kunit_speed_KUNIT_SPEED_NORMAL,
            },
            status_comment: [0; 256usize],
            debugfs: ::core::ptr::null_mut(),
            log: ::core::ptr::null_mut(),
            suite_init_err: 0,
            is_init: false,
        };

    /// Suite-pointer array in `.kunit_init_test_suites`, symbol name
    /// `_probe`-suffixed — the Rust equivalent of C's
    /// `kunit_test_init_section_suites()` (see module doc comment above).
    #[used(compiler)]
    #[link_section = ".kunit_init_test_suites"]
    static mut kunit_init_suites_array_rust_decompress_bunzip2_probe: [*mut ::kernel::bindings::kunit_suite; 1] =
        // SAFETY: `KUNIT_SUITE_rust_decompress_bunzip2_probe` is static.
        unsafe { [::core::ptr::addr_of_mut!(KUNIT_SUITE_rust_decompress_bunzip2_probe)] };
}
