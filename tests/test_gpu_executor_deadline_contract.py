#!/usr/bin/env python3
"""Focused static and compiled contract tests for executor request deadlines."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXECUTOR = ROOT / "app/src/main/cpp/pdocker_gpu_executor.c"


def c_function(source: str, name: str) -> str:
    marker = source.index(f"{name}(")
    start = source.rfind("static ", 0, marker)
    brace = source.index("{", marker)
    depth = 0
    for offset in range(brace, len(source)):
        if source[offset] == "{":
            depth += 1
        elif source[offset] == "}":
            depth -= 1
            if depth == 0:
                return source[start : offset + 1]
    raise AssertionError(f"unterminated C function: {name}")


class ExecutorDeadlineContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = EXECUTOR.read_text(encoding="utf-8")

    def test_all_request_receive_paths_share_one_monotonic_deadline(self) -> None:
        source = self.source
        start = c_function(source, "executor_request_deadline_start")
        remaining = c_function(source, "executor_request_deadline_remaining")
        client = c_function(source, "serve_socket_client_main")
        wait = c_function(source, "wait_for_executor_request_start")
        exact = c_function(source, "read_exact_bytes")
        command = c_function(source, "recv_command_with_fds")

        self.assertIn("clock_gettime(CLOCK_MONOTONIC, &now)", start)
        self.assertIn("clock_gettime(CLOCK_MONOTONIC, &now)", remaining)
        self.assertEqual(1, client.count("executor_request_deadline_start("))
        self.assertLess(
            client.index("wait_for_executor_request_start(cfd)"),
            client.index("executor_request_deadline_start("),
        )
        self.assertIn("recv(fd, &first, 1, MSG_PEEK)", wait)
        self.assertNotIn("deadline", wait)
        self.assertIn("recv_with_executor_request_deadline(", exact)

        for name in (
            "recv_vulkan_dispatch_v5_header_with_fds",
            "recv_vulkan_graphics_v6_header_with_fds",
            "recv_command_with_fds",
        ):
            body = c_function(source, name)
            self.assertIn("recvmsg_with_executor_request_deadline(", body)
            self.assertIn("executor_request_deadline_check()", body)
            self.assertNotIn("recvmsg(cfd", body)

        for name in (
            "connection_starts_with_v5_magic",
            "connection_starts_with_graphics_v6_magic",
        ):
            body = c_function(source, name)
            self.assertIn("recv_with_executor_request_deadline(", body)
            self.assertNotIn("poll(", body)
            self.assertNotIn("nanosleep", body)

        self.assertIn("MSG_PEEK", command)
        self.assertIn("memchr(cmd + off, '\\n', available)", command)
        self.assertNotIn("recv(cfd, &ch, 1", command)
        self.assertNotIn("poll(", command)

        self.assertIn(
            "read_exact_bytes(cfd, frame + sizeof(header), remaining)",
            c_function(source, "handle_vulkan_graphics_v6_frame"),
        )
        self.assertIn(
            "read_exact_bytes(cfd, frame + header_out->header_size, remaining)",
            c_function(source, "recv_vulkan_dispatch_v5_frame"),
        )

    def test_timeout_ends_stream_while_completed_requests_restore_idle_mode(self) -> None:
        client = c_function(self.source, "serve_socket_client_main")
        self.assertIn("if (graphics_rc != 0 || response_rc != 0) break;", client)
        self.assertIn("if (v5_rc != 0 || response_rc != 0) break;", client)
        self.assertIn("if (nread < 0)", client)
        self.assertGreaterEqual(client.count("set_executor_receive_timeout(cfd, 0)"), 3)
        self.assertIn("executor_request_deadline_clear();", client)
        self.assertIn("SO_SNDTIMEO", c_function(self.source, "configure_executor_client_socket"))

    def test_text_receive_failures_relinquish_all_received_fds(self) -> None:
        command = c_function(self.source, "recv_command_with_fds")
        cloexec = c_function(self.source, "ensure_received_fds_cloexec")
        close_received = c_function(self.source, "close_received_fds")

        entry_zero = "if (fd_count) *fd_count = 0;"
        entry_contracts = (
            ("recv_vulkan_dispatch_v5_header_with_fds", "if (!header ||"),
            ("recv_vulkan_dispatch_v5_frame", "if (!frame_out ||"),
            ("recv_vulkan_graphics_v6_header_with_fds", "if (!header ||"),
            ("recv_command_with_fds", "if (!cmd ||"),
        )
        for name, validation in entry_contracts:
            body = c_function(self.source, name)
            self.assertIn(entry_zero, body, name)
            self.assertLess(body.index(entry_zero), body.index(validation), name)

        v5_frame = c_function(self.source, "recv_vulkan_dispatch_v5_frame")
        self.assertLess(
            v5_frame.index("if (frame_out) *frame_out = NULL;"),
            v5_frame.index("if (!frame_out ||"),
        )

        truncated = command[
            command.index("if (n != 1 ||") : command.index("size_t seen_fds")
        ]
        self.assertIn("MSG_TRUNC | MSG_CTRUNC", truncated)
        self.assertIn("close(received[i]);", truncated)
        self.assertIn(
            "evidence->msg_trunc = (msg.msg_flags & MSG_TRUNC) != 0;",
            command,
        )
        self.assertIn(
            "evidence->msg_ctrunc = (msg.msg_flags & MSG_CTRUNC) != 0;",
            command,
        )
        self.assertIn(
            "int truncated_deadline_rc = executor_request_deadline_check();",
            truncated,
        )
        self.assertLess(
            truncated.index("executor_request_deadline_check()"),
            truncated.index("return -EMSGSIZE;"),
        )
        self.assertIn("return -EMSGSIZE;", truncated)

        deadline = command[
            command.index("int deadline_rc") : command.index("int cloexec_rc")
        ]
        self.assertIn("close_received_fds(passed_fds, fd_count);", deadline)
        self.assertIn("return deadline_rc;", deadline)

        self.assertIn("close(fds[j]);", cloexec)
        self.assertIn("fds[j] = -1;", cloexec)
        self.assertIn("close(fds[i]);", close_received)
        self.assertIn("fds[i] = -1;", close_received)
        self.assertIn("*count = 0;", close_received)

        line_receive = command[
            command.index("while (cmd[off - 1] != '\\n')") :
            command.index("cmd[off] = '\\0';")
        ]
        for return_marker in (
            "return -EMSGSIZE;",
            "return (int)n;",
            "return -EPROTO;",
            "return read_rc;",
        ):
            branch_end = line_receive.index(return_marker) + len(return_marker)
            branch_start = line_receive.rfind("if (", 0, branch_end)
            branch = line_receive[branch_start:branch_end]
            self.assertIn(
                "close_received_fds(passed_fds, fd_count);",
                branch,
                return_marker,
            )

    @unittest.skipUnless(shutil.which("gcc"), "gcc is required for the SCM_RIGHTS C harness")
    def test_compiled_fragmented_v5_v6_headers_and_timeout_release_fds(self) -> None:
        support_start = self.source.index("typedef struct {\n    struct timespec expires_at;")
        support_end = self.source.index("static int range_within_frame", support_start)
        support = self.source[support_start:support_end]
        ensure_cloexec = c_function(self.source, "ensure_received_fds_cloexec")
        close_received = c_function(self.source, "close_received_fds")
        recv_v5 = c_function(self.source, "recv_vulkan_dispatch_v5_header_with_fds")
        recv_v6 = c_function(self.source, "recv_vulkan_graphics_v6_header_with_fds")

        harness = "\n".join(
            [
                textwrap.dedent(
                    r"""
                    #define _GNU_SOURCE 1
                    #include <dirent.h>
                    #include <errno.h>
                    #include <fcntl.h>
                    #include <limits.h>
                    #include <pthread.h>
                    #include <signal.h>
                    #include <stdatomic.h>
                    #include <stdbool.h>
                    #include <stdint.h>
                    #include <stdio.h>
                    #include <stdlib.h>
                    #include <string.h>
                    #include <sys/socket.h>
                    #include <sys/syscall.h>
                    #include <sys/time.h>
                    #include <sys/types.h>
                    #include <time.h>
                    #include <unistd.h>

                    #define PDOCKER_GPU_MAX_PASSED_FDS 4

                    typedef struct {
                        unsigned char bytes[64];
                    } PdockerGpuVulkanDispatchV5FrameHeader;

                    typedef struct {
                        unsigned char bytes[80];
                    } PdockerGpuVulkanGraphicsV6FrameHeader;

                    static int validate_vulkan_dispatch_v5_header(
                            const PdockerGpuVulkanDispatchV5FrameHeader *header,
                            size_t fd_count) {
                        return header && fd_count == 1 &&
                               header->bytes[0] == 0x51 &&
                               header->bytes[sizeof(header->bytes) - 1] == 0xa5
                            ? 0 : -EPROTO;
                    }

                    static int validate_vulkan_graphics_v6_header_prefix(
                            const PdockerGpuVulkanGraphicsV6FrameHeader *header,
                            size_t fd_count) {
                        return header && fd_count == 1 &&
                               header->bytes[0] == 0x61 &&
                               header->bytes[sizeof(header->bytes) - 1] == 0xb6
                            ? 0 : -EPROTO;
                    }

                    static _Atomic int tracked_recvmsg_entered;
                    static _Atomic long tracked_first_recvmsg_result;
                    static _Atomic int tracked_first_recvmsg_errno;
                    static _Atomic long tracked_second_recvmsg_result;
                    static _Atomic int tracked_recvmsg_entries;

                    static ssize_t tracked_recvmsg(
                            int fd, struct msghdr *message, int flags) {
                        int entry_index = atomic_fetch_add(
                            &tracked_recvmsg_entries, 1);
                        atomic_store(&tracked_recvmsg_entered, 1);
                        ssize_t result = (ssize_t)syscall(
                            SYS_recvmsg, fd, message, flags);
                        int result_errno = result < 0 ? errno : 0;
                        if (entry_index == 0) {
                            atomic_store(
                                &tracked_first_recvmsg_errno, result_errno);
                            atomic_store(&tracked_first_recvmsg_result, (long)result);
                        } else if (entry_index == 1) {
                            atomic_store(&tracked_second_recvmsg_result, (long)result);
                        }
                        return result;
                    }

                    #define recvmsg tracked_recvmsg
                    """
                ),
                support,
                ensure_cloexec,
                close_received,
                recv_v5,
                recv_v6,
                textwrap.dedent(
                    r"""
                    #undef recvmsg

                    typedef int (*HeaderReceiver)(
                        int, unsigned char *, int *, size_t *);

                    static void sleep_ms(long milliseconds) {
                        struct timespec delay = {
                            .tv_sec = milliseconds / 1000,
                            .tv_nsec = (milliseconds % 1000) * 1000000l,
                        };
                        while (nanosleep(&delay, &delay) != 0 && errno == EINTR) {}
                    }

                    static int wait_for_atomic_value(
                            const _Atomic int *value, int expected, long timeout_ms) {
                        for (long elapsed = 0; elapsed < timeout_ms; ++elapsed) {
                            if (atomic_load(value) == expected) return 0;
                            sleep_ms(1);
                        }
                        return -ETIMEDOUT;
                    }

                    static int wait_for_recvmsg_result(long timeout_ms) {
                        for (long elapsed = 0; elapsed < timeout_ms; ++elapsed) {
                            if (atomic_load(&tracked_first_recvmsg_result) != LONG_MIN) return 0;
                            sleep_ms(1);
                        }
                        return -ETIMEDOUT;
                    }

                    static int wait_for_second_recvmsg_result(long timeout_ms) {
                        for (long elapsed = 0; elapsed < timeout_ms; ++elapsed) {
                            if (atomic_load(&tracked_second_recvmsg_result) != LONG_MIN) return 0;
                            sleep_ms(1);
                        }
                        return -ETIMEDOUT;
                    }

                    static int count_open_fds(void) {
                        DIR *directory = opendir("/proc/self/fd");
                        if (!directory) return -errno;
                        int count = 0;
                        struct dirent *entry;
                        while ((entry = readdir(directory)) != NULL) {
                            if (strcmp(entry->d_name, ".") != 0 &&
                                strcmp(entry->d_name, "..") != 0) {
                                count++;
                            }
                        }
                        closedir(directory);
                        return count;
                    }

                    static int send_fragment_with_fd(
                            int socket_fd,
                            const unsigned char *header,
                            size_t fragment_size,
                            int passed_fd) {
                        char control[CMSG_SPACE(sizeof(int))];
                        struct iovec iov = {
                            .iov_base = (void *)header,
                            .iov_len = fragment_size,
                        };
                        struct msghdr message;
                        memset(control, 0, sizeof(control));
                        memset(&message, 0, sizeof(message));
                        message.msg_iov = &iov;
                        message.msg_iovlen = 1;
                        message.msg_control = control;
                        message.msg_controllen = sizeof(control);
                        struct cmsghdr *cmsg = CMSG_FIRSTHDR(&message);
                        if (!cmsg) return -EIO;
                        cmsg->cmsg_level = SOL_SOCKET;
                        cmsg->cmsg_type = SCM_RIGHTS;
                        cmsg->cmsg_len = CMSG_LEN(sizeof(int));
                        memcpy(CMSG_DATA(cmsg), &passed_fd, sizeof(passed_fd));
                        message.msg_controllen = CMSG_SPACE(sizeof(int));
                        ssize_t sent = sendmsg(socket_fd, &message, MSG_NOSIGNAL);
                        if (sent < 0) return -errno;
                        return (size_t)sent == fragment_size ? 0 : -EIO;
                    }

                    static int send_exact(
                            int socket_fd, const unsigned char *data, size_t size) {
                        size_t offset = 0;
                        while (offset < size) {
                            ssize_t sent = send(
                                socket_fd, data + offset, size - offset, MSG_NOSIGNAL);
                            if (sent < 0 && errno == EINTR) continue;
                            if (sent < 0) return -errno;
                            if (sent == 0) return -EPIPE;
                            offset += (size_t)sent;
                        }
                        return 0;
                    }

                    static void fill_header(
                            unsigned char *header, size_t size, int graphics_v6) {
                        for (size_t i = 0; i < size; ++i) {
                            header[i] = (unsigned char)(i * 17u + (graphics_v6 ? 9u : 3u));
                        }
                        header[0] = graphics_v6 ? 0x61 : 0x51;
                        header[size - 1] = graphics_v6 ? 0xb6 : 0xa5;
                    }

                    static int receive_v5(
                            int socket_fd,
                            unsigned char *header_out,
                            int *passed_fds,
                            size_t *fd_count) {
                        PdockerGpuVulkanDispatchV5FrameHeader header;
                        int rc = recv_vulkan_dispatch_v5_header_with_fds(
                            socket_fd, &header, passed_fds,
                            PDOCKER_GPU_MAX_PASSED_FDS, fd_count);
                        if (rc == 0) memcpy(header_out, &header, sizeof(header));
                        return rc;
                    }

                    static int receive_v6(
                            int socket_fd,
                            unsigned char *header_out,
                            int *passed_fds,
                            size_t *fd_count) {
                        PdockerGpuVulkanGraphicsV6FrameHeader header;
                        bool header_received = false;
                        int rc = recv_vulkan_graphics_v6_header_with_fds(
                            socket_fd, &header, passed_fds,
                            PDOCKER_GPU_MAX_PASSED_FDS, fd_count,
                            &header_received);
                        if (rc == 0) memcpy(header_out, &header, sizeof(header));
                        return rc;
                    }

                    typedef struct {
                        int socket_fd;
                        HeaderReceiver receiver;
                        unsigned char header[80];
                        int passed_fds[PDOCKER_GPU_MAX_PASSED_FDS];
                        size_t fd_count;
                        int rc;
                    } ReceiveArgs;

                    static void *receive_fragmented_header(void *opaque) {
                        ReceiveArgs *args = (ReceiveArgs *)opaque;
                        args->rc = executor_request_deadline_start(800000000ull);
                        if (args->rc == 0) {
                            args->rc = args->receiver(
                                args->socket_fd, args->header,
                                args->passed_fds, &args->fd_count);
                        }
                        return NULL;
                    }

                    static void interrupt_handler(int signal_number) {
                        (void)signal_number;
                    }

                    static int run_fragmented_interrupt_case(
                            HeaderReceiver receiver,
                            size_t header_size,
                            int graphics_v6) {
                        int sockets[2];
                        int pipe_fds[2];
                        if (socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) != 0) return 10;
                        if (pipe(pipe_fds) != 0) return 11;

                        unsigned char expected[80];
                        fill_header(expected, header_size, graphics_v6);
                        ReceiveArgs args;
                        memset(&args, 0, sizeof(args));
                        args.socket_fd = sockets[0];
                        args.receiver = receiver;
                        args.rc = -1;
                        for (size_t i = 0; i < PDOCKER_GPU_MAX_PASSED_FDS; ++i) {
                            args.passed_fds[i] = -1;
                        }
                        atomic_store(&tracked_recvmsg_entered, 0);
                        atomic_store(&tracked_first_recvmsg_result, LONG_MIN);
                        atomic_store(&tracked_first_recvmsg_errno, 0);
                        atomic_store(&tracked_second_recvmsg_result, LONG_MIN);
                        atomic_store(&tracked_recvmsg_entries, 0);

                        pthread_t receiver_thread;
                        if (pthread_create(
                                &receiver_thread, NULL,
                                receive_fragmented_header, &args) != 0) return 12;
                        if (wait_for_atomic_value(
                                &tracked_recvmsg_entered, 1, 300) != 0) return 13;

                        /* Interrupt the empty MSG_WAITALL first.  The helper
                         * must preserve the same absolute deadline and restore
                         * msghdr capacities before entering recvmsg again. */
                        if (pthread_kill(receiver_thread, SIGUSR1) != 0) return 14;
                        if (wait_for_recvmsg_result(300) != 0) return 15;
                        if (atomic_load(&tracked_first_recvmsg_result) != -1 ||
                            atomic_load(&tracked_first_recvmsg_errno) != EINTR) {
                            return 16;
                        }
                        if (wait_for_atomic_value(
                                &tracked_recvmsg_entries, 2, 300) != 0) return 17;

                        /* SCM_RIGHTS forms a stream control boundary on Linux,
                         * so this retry returns a short positive header even
                         * with MSG_WAITALL.  Production then reads the tail
                         * without receiving the descriptor a second time. */
                        const size_t fragment_size = 7;
                        int rc = send_fragment_with_fd(
                            sockets[1], expected, fragment_size, pipe_fds[0]);
                        if (rc != 0) return 18;
                        if (wait_for_second_recvmsg_result(300) != 0) return 19;
                        long short_result = atomic_load(
                            &tracked_second_recvmsg_result);
                        if (short_result <= 0 ||
                            (size_t)short_result >= header_size) return 20;

                        rc = send_exact(
                            sockets[1], expected + fragment_size,
                            header_size - fragment_size);
                        if (rc != 0) return 21;
                        if (pthread_join(receiver_thread, NULL) != 0) return 22;
                        if (args.rc != 0 || args.fd_count != 1) return 23;
                        if (memcmp(args.header, expected, header_size) != 0) return 24;
                        int descriptor_flags = fcntl(args.passed_fds[0], F_GETFD);
                        if (descriptor_flags < 0 ||
                            (descriptor_flags & FD_CLOEXEC) == 0) return 25;

                        close_received_fds(args.passed_fds, &args.fd_count);
                        close(pipe_fds[0]);
                        close(pipe_fds[1]);
                        close(sockets[0]);
                        close(sockets[1]);
                        return 0;
                    }

                    static int run_partial_timeout_case(
                            HeaderReceiver receiver,
                            size_t header_size,
                            int graphics_v6) {
                        int sockets[2];
                        int pipe_fds[2];
                        if (socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) != 0) return 30;
                        if (pipe(pipe_fds) != 0) return 31;

                        unsigned char expected[80];
                        unsigned char received[80];
                        int passed_fds[PDOCKER_GPU_MAX_PASSED_FDS];
                        size_t fd_count = 0;
                        fill_header(expected, header_size, graphics_v6);
                        memset(received, 0, sizeof(received));
                        for (size_t i = 0; i < PDOCKER_GPU_MAX_PASSED_FDS; ++i) {
                            passed_fds[i] = -1;
                        }
                        int rc = send_fragment_with_fd(
                            sockets[1], expected, 7, pipe_fds[0]);
                        if (rc != 0) return 32;
                        int before = count_open_fds();
                        if (before < 0) return 33;
                        if (executor_request_deadline_start(120000000ull) != 0) return 34;
                        rc = receiver(sockets[0], received, passed_fds, &fd_count);
                        int after = count_open_fds();
                        if (rc != -ETIMEDOUT) return 35;
                        if (fd_count != 0) return 36;
                        for (size_t i = 0; i < PDOCKER_GPU_MAX_PASSED_FDS; ++i) {
                            if (passed_fds[i] != -1) return 37;
                        }
                        if (after != before) return 38;

                        close(pipe_fds[0]);
                        close(pipe_fds[1]);
                        close(sockets[0]);
                        close(sockets[1]);
                        return 0;
                    }

                    int main(void) {
                        struct sigaction action;
                        memset(&action, 0, sizeof(action));
                        action.sa_handler = interrupt_handler;
                        sigemptyset(&action.sa_mask);
                        action.sa_flags = 0;
                        if (sigaction(SIGUSR1, &action, NULL) != 0) return 2;
                        signal(SIGPIPE, SIG_IGN);

                        int invalid_fds[PDOCKER_GPU_MAX_PASSED_FDS] = {-1, -1, -1, -1};
                        size_t invalid_fd_count = 9;
                        if (recv_vulkan_dispatch_v5_header_with_fds(
                                -1, NULL, invalid_fds,
                                PDOCKER_GPU_MAX_PASSED_FDS,
                                &invalid_fd_count) != -EINVAL ||
                            invalid_fd_count != 0) {
                            return 3;
                        }
                        invalid_fd_count = 9;
                        bool invalid_header_received = true;
                        if (recv_vulkan_graphics_v6_header_with_fds(
                                -1, NULL, invalid_fds,
                                PDOCKER_GPU_MAX_PASSED_FDS,
                                &invalid_fd_count,
                                &invalid_header_received) != -EINVAL ||
                            invalid_fd_count != 0 ||
                            invalid_header_received) {
                            return 4;
                        }

                        int rc = run_fragmented_interrupt_case(
                            receive_v5,
                            sizeof(PdockerGpuVulkanDispatchV5FrameHeader), 0);
                        if (rc != 0) {
                            fprintf(stderr, "fragmented V5 failed: %d\n", rc);
                            return rc;
                        }
                        rc = run_fragmented_interrupt_case(
                            receive_v6,
                            sizeof(PdockerGpuVulkanGraphicsV6FrameHeader), 1);
                        if (rc != 0) {
                            fprintf(stderr, "fragmented V6 failed: %d\n", rc);
                            return rc;
                        }
                        rc = run_partial_timeout_case(
                            receive_v5,
                            sizeof(PdockerGpuVulkanDispatchV5FrameHeader), 0);
                        if (rc != 0) {
                            fprintf(stderr, "timeout V5 failed: %d\n", rc);
                            return rc;
                        }
                        rc = run_partial_timeout_case(
                            receive_v6,
                            sizeof(PdockerGpuVulkanGraphicsV6FrameHeader), 1);
                        if (rc != 0) {
                            fprintf(stderr, "timeout V6 failed: %d\n", rc);
                            return rc;
                        }
                        printf(
                            "fragmented_v5=ok fragmented_v6=ok "
                            "timeout_v5_no_leak=ok timeout_v6_no_leak=ok\n");
                        return 0;
                    }
                    """
                ),
            ]
        )

        with tempfile.TemporaryDirectory(prefix="gpu-executor-scm-rights-") as tmp:
            source_path = Path(tmp) / "scm_rights_harness.c"
            binary_path = Path(tmp) / "scm_rights_harness"
            source_path.write_text(harness, encoding="utf-8")
            compile_result = subprocess.run(
                [
                    "gcc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-function", "-pthread",
                    str(source_path), "-o", str(binary_path),
                ],
                text=True, capture_output=True, timeout=30, check=False,
            )
            self.assertEqual(0, compile_result.returncode, compile_result.stderr)
            result = subprocess.run(
                [str(binary_path)], text=True, capture_output=True,
                timeout=10, check=False,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("fragmented_v5=ok", result.stdout)
        self.assertIn("fragmented_v6=ok", result.stdout)
        self.assertIn("timeout_v5_no_leak=ok", result.stdout)
        self.assertIn("timeout_v6_no_leak=ok", result.stdout)

    @unittest.skipUnless(shutil.which("gcc"), "gcc is required for the deadline C harness")
    def test_compiled_socket_harness_rejects_slow_drip_but_allows_idle(self) -> None:
        support_start = self.source.index("typedef struct {\n    struct timespec expires_at;")
        support_end = self.source.index("static int range_within_frame", support_start)
        support = self.source[support_start:support_end]
        wait = c_function(self.source, "wait_for_executor_request_start")
        harness = textwrap.dedent(
            f"""
            #define _POSIX_C_SOURCE 200809L
            #include <errno.h>
            #include <limits.h>
            #include <pthread.h>
            #include <signal.h>
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include <sys/socket.h>
            #include <sys/time.h>
            #include <time.h>
            #include <unistd.h>

            {support}
            {wait}

            static double monotonic_seconds(void) {{
                struct timespec now;
                if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return -1.0;
                return (double)now.tv_sec + (double)now.tv_nsec / 1000000000.0;
            }}

            static void sleep_ms(long milliseconds) {{
                struct timespec delay = {{
                    .tv_sec = milliseconds / 1000,
                    .tv_nsec = (milliseconds % 1000) * 1000000l,
                }};
                while (nanosleep(&delay, &delay) != 0 && errno == EINTR) {{}}
            }}

            typedef struct {{ int fd; int rc; double idle_seconds; }} IdleArgs;

            static void *idle_reader(void *opaque) {{
                IdleArgs *args = (IdleArgs *)opaque;
                double before = monotonic_seconds();
                int start_rc = wait_for_executor_request_start(args->fd);
                args->idle_seconds = monotonic_seconds() - before;
                if (start_rc != 1) {{ args->rc = start_rc ? start_rc : -ECONNRESET; return NULL; }}
                args->rc = executor_request_deadline_start(120000000ull);
                char request[5];
                if (args->rc == 0) args->rc = read_exact_bytes(args->fd, request, sizeof(request));
                if (args->rc == 0 && memcmp(request, "NOOP\\n", 5) != 0) args->rc = -EPROTO;
                return NULL;
            }}

            typedef struct {{ int fd; }} DripArgs;

            static void *drip_writer(void *opaque) {{
                DripArgs *args = (DripArgs *)opaque;
                const char payload[] = "12345678";
                for (size_t i = 0; i < sizeof(payload) - 1; ++i) {{
                    if (i != 0) sleep_ms(50);
                    if (send(args->fd, payload + i, 1, 0) != 1) break;
                }}
                return NULL;
            }}

            int main(void) {{
                signal(SIGPIPE, SIG_IGN);
                int idle_pair[2];
                if (socketpair(AF_UNIX, SOCK_STREAM, 0, idle_pair) != 0) return 2;
                IdleArgs idle = {{ .fd = idle_pair[0], .rc = -1, .idle_seconds = 0.0 }};
                pthread_t idle_thread;
                if (pthread_create(&idle_thread, NULL, idle_reader, &idle) != 0) return 3;
                sleep_ms(260);
                if (send(idle_pair[1], "NOOP\\n", 5, 0) != 5) return 4;
                if (pthread_join(idle_thread, NULL) != 0) return 5;
                close(idle_pair[0]); close(idle_pair[1]);
                if (idle.rc != 0 || idle.idle_seconds < 0.22) {{
                    fprintf(stderr, "idle rc=%d elapsed=%.6f\\n", idle.rc, idle.idle_seconds);
                    return 6;
                }}

                int drip_pair[2];
                if (socketpair(AF_UNIX, SOCK_STREAM, 0, drip_pair) != 0) return 7;
                DripArgs drip = {{ .fd = drip_pair[1] }};
                pthread_t drip_thread;
                if (pthread_create(&drip_thread, NULL, drip_writer, &drip) != 0) return 8;
                if (wait_for_executor_request_start(drip_pair[0]) != 1) return 9;
                if (executor_request_deadline_start(160000000ull) != 0) return 10;
                char bytes[8];
                double before = monotonic_seconds();
                int rc = read_exact_bytes(drip_pair[0], bytes, sizeof(bytes));
                double elapsed = monotonic_seconds() - before;
                close(drip_pair[0]); close(drip_pair[1]);
                if (pthread_join(drip_thread, NULL) != 0) return 11;
                if (rc != -ETIMEDOUT || elapsed < 0.10 || elapsed > 0.32) {{
                    fprintf(stderr, "drip rc=%d elapsed=%.6f\\n", rc, elapsed);
                    return 12;
                }}
                printf("idle=%.3f slow_drip=%.3f rc=%d\\n", idle.idle_seconds, elapsed, rc);
                return 0;
            }}
            """
        )
        with tempfile.TemporaryDirectory(prefix="gpu-executor-deadline-") as tmp:
            source_path = Path(tmp) / "deadline_harness.c"
            binary_path = Path(tmp) / "deadline_harness"
            source_path.write_text(harness, encoding="utf-8")
            compile_result = subprocess.run(
                ["gcc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                 "-Wno-unused-function", "-pthread", str(source_path), "-o", str(binary_path)],
                text=True, capture_output=True, timeout=30, check=False,
            )
            self.assertEqual(0, compile_result.returncode, compile_result.stderr)
            result = subprocess.run(
                [str(binary_path)], text=True, capture_output=True, timeout=10, check=False
            )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("rc=-110", result.stdout)


if __name__ == "__main__":
    unittest.main()
