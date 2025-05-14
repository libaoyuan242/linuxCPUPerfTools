import re, subprocess, threading, queue
from collections import namedtuple, deque
from datetime import datetime,timedelta
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import CheckButtons,Button
import matplotlib.gridspec as gridspec


# 全局常量
filename = "/Users/libaoyuan/Desktop/123"
topFilename = "CPUData/top_" + str(datetime.now().strftime("%Y%m%d.%H%M%S")) + ".log"
dataSrcType = 1  # 0: 文件, 1: 实时数据
MAX_DATA_POINTS = 60  # 保留最近60个数据点
MAX_PROCESS_NUM = 5  # 最大进程数
ANI_FRAME = 1000000
flag_data = True
flag_screen = True
flag_end = False
min_cpu = 0.06

selected_processes = []
excluded_processes = ["prank"]



# 定义数据结构
MemInfo = namedtuple('MemInfo', ['used', 'free', 'shrd', 'buff', 'cached'])
CpuInfo = namedtuple('CpuInfo', ['usr', 'sys', 'nic', 'idle', 'io', 'irq', 'sirq', 'st'])
LoadInfo = namedtuple('LoadInfo', ['load1', 'load5', 'load15', 'running', 'total', 'highest_pid', 'time'])
ProcessInfo = namedtuple('ProcessInfo', ['pid', 'ppid', 'user', 'stat', 'vsz', 'vsz_percent', 'cpu', 'cpu_percent', 'command'])
plt.rc("font",family='STHeiti',weight='bold', size=8)

class TopParser:
    """解析 top 数据的类"""

    def __init__(self,src_type, content):
        self.data = {}
        self.src_type = src_type  # 0: 文件, 1: 实时数据
        self.srcContent = content

    def parse_mem_line(self, line):
        """解析内存行"""
        match = re.match(
            r'Mem:\s+(\d+)K used,\s+(\d+)K free,\s+(\d+)K shrd,\s+(\d+)K buff,\s+(\d+)K cached',
            line
        )
        return MemInfo(*map(int, match.groups())) if match else None

    def parse_cpu_line(self, line):
        """解析CPU行"""
        match = re.match(
            r'CPU:\s+([\d.]+)% usr\s+([\d.]+)% sys\s+([\d.]+)% nic\s+([\d.]+)% idle\s+'
            r'([\d.]+)% io\s+([\d.]+)% irq\s+([\d.]+)% sirq\s+([\d.]+)% st',
            line
        )
        return CpuInfo(*map(float, match.groups())) if match else None

    def parse_load_line(self, line):
        """解析负载行"""
        match = re.match(
            r'Load average:\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(\d+)/(\d+)\s+(\d+),\s+([\d:]+)',
            line
        )
        if match:
            return LoadInfo(
                float(match.group(1)), float(match.group(2)), float(match.group(3)),
                int(match.group(4)), int(match.group(5)), int(match.group(6)), match.group(7)
            )
        return None

    def parse_process_line(self, line):
        """解析进程行"""
        match = re.match(
            r'\s*(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+([\d.]+)\s+(\d+)\s+([\d.]+)\s+(.*)',
            line
        )
        if match:
            return ProcessInfo(
                int(match.group(1)), int(match.group(2)), match.group(3), match.group(4),
                match.group(5), float(match.group(6)), int(match.group(7)),
                float(match.group(8)), match.group(9)
            )
        return None

    def parse_top(self, lines):
        """解析 top 数据"""
        processes = []
        curr_time = None
        
        for line in lines.splitlines("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("Mem:"):
                self.parse_mem_line(line)
                if curr_time:
                    self.update_data(curr_time, processes)
                processes.clear()
            elif line.startswith("CPU:"):
                cpu_info = self.parse_cpu_line(line)
                if cpu_info:
                    processes.append(("Total CPU", round( 100 - cpu_info.idle + 4, 2)))
            elif line.startswith("Load average:"):
                load_info = self.parse_load_line(line)
                if load_info:
                    curr_time = load_info.time
            elif line.startswith("PID") or line.startswith("  PID"):
                continue  # 跳过表头
            else:
                if len(processes) >= MAX_PROCESS_NUM:
                    continue
                process = self.parse_process_line(line)
                if process and process.cpu_percent > min_cpu :
                    processes.append((process.command, process.cpu_percent))
                    # if process.command.startswith("/usr/bin/weston"):
                    #     print("weston :",process.cpu_percent)

        
        if curr_time:
            self.update_data(curr_time, processes)
            
    def cmdDeal(self, cmd):
        """处理命令行"""
        if cmd.startswith("/usr/bin/"):
            index = cmd.find(" ")
            index =  None if index == -1 else index
            cmd = cmd[9:index].strip()
        elif cmd.startswith("{page://"):
            index = cmd.find(" ")
            cmd = cmd[8:index-1].strip()
            
        return cmd

    def update_data(self, timestamp, processes):
        """更新存储的数据"""
        min_time=''
        total_cmd="Total CPU"
        flagdel = False

        for cmd, cpu in processes:  
            cmd = self.cmdDeal(cmd)
            if cmd in excluded_processes or  (cmd !=total_cmd  and len(selected_processes) >0  and cmd not in selected_processes):
                continue
            
            if cmd not in self.data:
                self.data[cmd] = {"timestamps": [], "cpu_usage": []}
            
            self.data[cmd]["timestamps"].append(timestamp)
            self.data[cmd]["cpu_usage"].append(cpu)
            
        if len(self.data) > 0 and len(self.data[total_cmd]["timestamps"]) > MAX_DATA_POINTS:
            min_time = self.data[total_cmd]["timestamps"].pop(0)
            self.data[total_cmd]["cpu_usage"].pop(0)
            
            for k, v in self.data.items():
                while v['timestamps'][0] <= min_time:
                    time = v['timestamps'].pop(0)
                    v['cpu_usage'].pop(0)
                    if(len(v['timestamps']) == 0):
                        flagdel = True
                        break
            if flagdel :
                self.data = {k: v for k, v in self.data.items() if len(v['timestamps']) != 0}


class CPUUsageMonitor:
    """CPU 使用监控器"""
    def __init__(self, parser):
        self.parser = parser
        self.lines = []
        self.labels = []
        self.selected_lable = None
        self.is_paused = False
        self.check = None
        self.rax = None

        # 初始化图形
        self.fig = plt.figure( figsize=(12, 6))
        self.fig.canvas.manager.set_window_title('Process CPU Usage Monitor')
        # 使用 GridSpec 来定义子图的布局
        gs = gridspec.GridSpec(2, 1, height_ratios=[1, 2])
        self.ax1 = self.fig.add_subplot(gs[0], sharex=None)
        self.ax2 = self.fig.add_subplot(gs[1], sharex=self.ax1)
        self.configure_axis()
        
        # 绑定点击事件
        self.fig.canvas.mpl_connect('pick_event', self.on_pick)
        self.fig.canvas.mpl_connect('button_press_event', self.on_click)
        self.fig.canvas.mpl_connect('key_press_event', self.on_key)
        self.fig.canvas.mpl_connect('close_event', self.on_close)

        
        
    def select_all(self, event, flag=None):
        """全选按钮的回调函数"""
        for i, f in enumerate(self.check.get_status()):
            if f == True and flag == "all":
                continue
            else:
                self.check.set_active(i)
                
        plt.draw()
        
        
    def calculate_time_difference(self, time_str1, time_str2):
        # 定义时间格式
        time_format = "%H:%M:%S"
        
        # 将字符串解析为 datetime 对象
        time1 = datetime.strptime(time_str1, time_format)
        time2 = datetime.strptime(time_str2, time_format)
        
        # 计算时间差
        time_diff = time2 - time1
        
        # 返回时间差
        return time_diff

    def update_graph(self, frame):
        """更新图表"""
        if self.is_paused or frame == 0 or ( self.parser.src_type == 0 and frame >1):
            return
        
        # 解析数据
        if self.parser.src_type == 0:
            self.parser.parse_top(self.parser.srcContent)
        else:
            self.parser.parse_top(self.parser.srcContent.get_top_data())
            
        self.ax1.clear()
        self.ax2.clear()
        self.lines.clear()
        self.ax1.grid(axis='y', color='lightgray', linestyle='--', alpha=0.7)
        self.ax2.grid(axis='y', color='lightgray', linestyle='--', alpha=0.7)
        
        # 绘制数据
        colors = plt.cm.tab20.colors
        lineNum =  len(self.parser.data.items())
        lasttime = 0
        for idx, (cmd, values) in enumerate(self.parser.data.items()):
            valid_points = [(t, v) for t, v in zip(values["timestamps"], values["cpu_usage"]) if v is not None]
            if valid_points:
                times, cpus = zip(*valid_points)
                if cmd == 'Total CPU':
                    lasttime = times[-1]
                    length = 5 if len(cpus) > 5 else len(cpus)
                    lable = "CPU : 【" + str(round(sum(cpus[-length:])/length,2)) + "】"
                    self.ax1.plot(times, cpus, color='gray', label=lable, linewidth=2, marker='o', markersize=4)
                    for i, v in enumerate(times):
                        self.ax1.text(times[i], cpus[i], f'{cpus[i]}', ha='center', va='bottom')
                else:
                    length = 10 if len(cpus) > 10 else len(cpus)
                    cmd = cmd[:8].ljust(8) + " 【" + str(round(sum(cpus[-length:])/length,2)) + "】"
                    if self.calculate_time_difference(times[-1], lasttime) > timedelta(seconds=10):
                        cmd = cmd + " # "
                        if len(cpus) < 15:
                            continue
                        
                    line, = self.ax2.plot(times, cpus, color=colors[idx % len(colors)],
                                          label=f"{cmd[:20]}" if len(cmd) > 20 else cmd,
                                          linewidth=2, picker=5, marker='o', markersize=4)
                    
                    if self.selected_lable is not None:
                        if self.selected_lable[:8] != cmd[:8]:
                            line.set_alpha(0.3)
                        else:
                            for i, v in enumerate(times):
                                self.ax2.text(times[i], cpus[i], f'{cpus[i]}', ha='center', va='bottom')
                        
                    if(lineNum < 5):
                        for i, v in enumerate(times):
                            self.ax2.text(times[i], cpus[i], f'{cpus[i]}', ha='center', va='bottom')
                    
                    self.lines.append(line)
        
        if len(self.lines) > 0:
            self.ax1.legend(loc='upper left', fontsize=8)
            self.ax2.legend(loc='upper left', fontsize=8)
            if frame == ANI_FRAME-1 or self.parser.src_type == 0:
                self.labels = [line.get_label() for line in self.lines]  # 更新复选框
                self.init_checkbuttons()
                

    def configure_axis(self):
        self.ax1.spines['top'].set_visible(False)
        self.ax1.spines['bottom'].set_visible(False)
        self.ax1.spines['left'].set_visible(False)
        self.ax2.spines['left'].set_visible(False)
        self.ax2.spines['top'].set_visible(False)
        self.ax1.set_facecolor('#f0f0f0')
        self.ax2.set_facecolor('#f0f0f0')
        self.ax1.xaxis.set_visible(False)
        self.ax2.tick_params(axis='x', rotation=45)
        self.ax1.yaxis.tick_right()
        self.ax2.yaxis.tick_right()
        self.ax2.legend(loc='upper left', fontsize=8, frameon=False)
        
        plt.subplots_adjust(left=0.08, right=0.97)


    def init_checkbuttons(self):
        """初始化或更新 CheckButtons"""
        self.rax = plt.axes([0.01, 0.08, 0.06, 0.83])  # CheckButtons 区域
        button_all = plt.axes([0.01, 0.02, 0.03 ,0.03])
        button_reverse = plt.axes([0.05, 0.02, 0.03 ,0.03])
        self.rax.set_axis_off()
        
        self.select_all_button = Button(button_all, '全选', )
        self.select_reverse_button = Button(button_reverse, '反选')
        self.select_all_button.on_clicked(lambda event,flag=True: self.select_all(event, 'all'))
        self.select_reverse_button.on_clicked(self.select_all)
        
        if self.check is None:
            self.check = CheckButtons(self.rax, self.labels, [True] * len(self.labels))
            self.check.on_clicked(self.toggle_lines)


    def toggle_lines(self, label):
        """复选框回调函数"""
        for num,line in enumerate(self.lines):
            line.set_alpha(1)
        
        index = self.labels.index(label)
        self.lines[index].set_visible(not self.lines[index].get_visible())
        plt.draw()


    # 点击事件处理函数
    def on_pick(self, event):
        # 隐藏所有图例项
        self.ax2.legend().set_visible(False)
        
        # 获取被点击的线条
        label = event.artist.get_label()

        for index, line in enumerate( self.lines):
            if line.get_label() == label:
                legend_line = plt.Line2D([], [], color=line.get_color(), label=line.get_label())
                self.ax2.legend(handles=[legend_line])
                line.set_alpha(1)
                self.selected_lable = label
                
                map_timespace= list(self.parser.data.values())[index+1]["timestamps"]
                map_cpu= list(self.parser.data.values())[index+1]["cpu_usage"]
                for i, v in enumerate(map_timespace):
                    self.ax2.text(v, map_cpu[i], f'{map_cpu[i]}', ha='center', va='bottom')
                
            else:
                line.set_alpha(0.3)  # 其他线条变淡
                
        plt.draw()
        
        
    # 添加一个点击空白处恢复的函数
    def on_click(self, event):
        if event.inaxes != self.ax2:
            self.ax2.legend().set_visible(False)
            for i in self.ax2.texts:
                i.remove()
            
            if self.rax is None or (self.rax and event.inaxes != self.rax):
                # 恢复所有线条的透明度
                for line in self.lines:
                    line.set_alpha(1.0)
                self.selected_lable = None
                
            plt.draw()
            
            
    # 键盘事件处理函数
    def on_key(self, event):
        if event.key == ' ':  # 按空格键暂停/继续
            self.is_paused = not self.is_paused
        elif event.key == 'ctrl+c':
            self.parser.data.clear()
            pass
        
    def on_close(self, event):
        global flag_end
        flag_end = True
        


class RealTimeTopData:
    """实时获取 top 数据的类"""
    def __init__(self):
        self.cmdStr = "adb -host shell ctop -b -d 1"
        self.output_queue = queue.Queue()
        self.output_tmp = ""
        pass
    
    # def get_top_data(self):
    #     """获取实时 top 数据"""
    #     # 这里可以实现实时获取 top 数据的逻辑
    #     # 例如使用 subprocess 模块调用 top 命令
    #     result = subprocess.run(['adb','-host', 'shell','ctop', '-b', '-n', '1'], stdout=subprocess.PIPE)
    #     outstr = result.stdout.decode('utf-8').splitlines()
    #     # print(outstr)
    #     if flag_data:
    #         self.write_top_data(outstr)
    #     if flag_screen:
    #         self.capture_screen()
    #     return outstr
    
    def get_top_data(self):
        output = ""
        linenum = 0
        while True:
            line = self.output_queue.get()
            if line is None:  # 接收到结束信号
                break
            elif line.startswith("Mem:") and linenum > 1:
                output = self.output_tmp
                self.output_tmp = ""
                self.output_tmp += line + "\n"
                if flag_data:
                    self.write_top_data(output)
                return output
            
            self.output_tmp += line + "\n"
            linenum += 1

    
    def execute_command(self):
        """执行 Shell 命令并将输出放入队列"""
        try:
            process = subprocess.Popen(
                self.cmdStr,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            
            for line in process.stdout:
                self.output_queue.put(line.strip())
                if flag_end:
                    break

            process.terminate()  # 终止进程
            self.output_queue.put(None)  # 发送结束信号
        except Exception as e:
            self.output_queue.put(f"Error executing command: {e}")
            self.output_queue.put(None)
    
    def write_top_data(self, data):
        """写入 top 数据到文件"""
        with open(topFilename, 'a') as file:
            for num,line in enumerate(data):
                if num > 100:
                    break
                file.write(line + '\n')
                
    def capture_screen(self):
        """ 捕获屏幕截图 """
        screen_name = "/private/liby/screen" + str(datetime.now().strftime("%m%d.%H%M%S")) + ".png"
        result = subprocess.run(['adb','-host', 'shell','weston-screenshooter', screen_name])


if __name__ == "__main__":
    
    if dataSrcType == 0:
        # 读取文件
        try:
            with open(filename, 'r') as file:
                lines = file.readlines()
            parser = TopParser(dataSrcType, lines)
        except FileNotFoundError:
            print(f"Error: File '{filename}' not found.")
            exit
        except Exception as e:
            print(f"Error reading file: {e}")
            exit
    else:
        # 初始化解析器和监控器
        rtdata = RealTimeTopData()
        parser = TopParser(dataSrcType, rtdata)
        # 创建并启动线程  异步执行 Shell 命令并实时打印输出
        executor = threading.Thread(target=rtdata.execute_command)
        executor.start()
        
    
    monitor = CPUUsageMonitor(parser)
    
    # 创建动画
    ani = FuncAnimation(monitor.fig, monitor.update_graph, interval=1000, frames=ANI_FRAME, cache_frame_data=False, repeat=False)
    
    # 显示图形
    plt.show()
    print("程序结束")
    
    # 等待线程完成
    executor.join()
    print("程序结束")