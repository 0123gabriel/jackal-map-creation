import random
import datetime
import Queue
import math

import matplotlib.pyplot as plt
import Tkinter as tk
import numpy as np

from world_writer import WorldWriter
from difficulty_quant import DifficultyMetrics
from pgm_writer import PGMWriter
from yaml_writer import YamlWriter

# jackal takes up 2 extra grid squares on each side in addition to center square
jackal_radius = 2

# pgm file resolution
pgm_res = 0.15 # meters per pixel

# inflation radius found in planner params
infl_rad = 0.3 # meters

class ObstacleMap():
  def __init__(self, rows, cols, rand_fill_pct, seed=None, smooth_iter=5):
    self.map = [[0 for i in range(cols)] for j in range(rows)]
    self.rows = rows
    self.cols = cols
    self.rand_fill_pct = rand_fill_pct
    self.seed = seed
    self.smooth_iter = smooth_iter

  def __call__(self):
    self._random_fill()
    for n in range(self.smooth_iter):
      self._smooth()

  def _random_fill(self):
    if self.seed:
      random.seed(self.seed)

    for r in range(self.rows):
      for c in range(self.cols):
        if r == 0 or r == self.rows - 1:
          self.map[r][c] = 1
        else:
          self.map[r][c] = 1 if random.random() < self.rand_fill_pct else 0

  def _smooth(self):
    newmap = [[self.map[r][c] for c in range(self.cols)] for r in range(self.rows)]
    for r in range(self.rows):
      for c in range(self.cols):
        # if more than 4 filled neighbors, fill this tile
        if self._tile_neighbors(r, c) > 4:
          newmap[r][c] = 1

        # if less than 2 filled neighbors, empty this one
        elif self._tile_neighbors(r, c) < 2:
          newmap[r][c] = 0

    self.map = newmap

  def _tile_neighbors(self, r, c):
    count = 0
    for i in range(r - 1, r + 2):
      for j in range(c - 1, c + 2):
        if self._in_map(i, j):
          if i != r or j != c:
            count += self.map[i][j]

        # if on the top or bottom, add to wall neighbors
        elif i < 0 or i >= self.rows:
          count += 1
    
    return count

  def _in_map(self, r, c):
    return r >= 0 and r < self.rows and c >= 0 and c < self.cols

  def get_map(self):
    return self.map

class JackalMap:
  def __init__(self, ob_map, robot_radius):
    self.ob_map = ob_map
    self.rows = len(ob_map)
    self.cols = len(ob_map[0])

    self.map = self._jmap_from_obs_map(robot_radius)
    self.infl_rad_cells = self.calc_infl_rad_cells()

  # use flood-fill algorithm to find the open region including (r, c)
  def _get_region(self, r, c):
    queue = Queue.Queue(maxsize=0)
    
    # region is 2D array that indicates the open region connected to (r, c) with a 1
    region = [[0 for i in range(self.cols)] for j in range(self.rows)]
    size = 0

    if self.map[r][c] == 0:
      queue.put((r, c))
      region[r][c] = 1
      size += 1

    while not queue.empty():
      coord_r, coord_c = queue.get()

      # check four cardinal neighbors
      for i in range(coord_r-1, coord_r+2):
        for j in range(coord_c-1, coord_c+2):
          if self._in_map(i, j) and (i == coord_r or j == coord_c):
            # if empty space and not checked yet
            if self.map[i][j] == 0 and region[i][j] == 0:
              # add to region and put in queue
              region[i][j] = 1
              queue.put((i, j))
              size += 1

    return region, size

  # returns the largest contiguous region with a tile in the leftmost column
  def biggest_left_region(self):
    max_size = 0
    max_region = []
    for row in range(self.rows):
      region, size = self._get_region(row, 0)

      if size > max_size:
        max_size = size
        max_region = region

    # no region available, just generate random open spot
    if max_size == 0:
      random_row = random.randint(1, self.rows - 1)
      self.map[random_row][0] = 0

      max_region = [[0 for i in range(self.cols)] for j in range(self.rows)]
      max_region[random_row][0] = 1

    return max_region

  # returns the largest contiguous region with a tile in the rightmost column
  def biggest_right_region(self):
    max_size = 0
    max_region = []
    for row in range(self.rows):
      region, size = self._get_region(row, self.cols-1)

      if size > max_size:
        max_size = size
        max_region = region

    # no region available, just generate random open spot
    if max_size == 0:
      random_row = random.randint(1, self.rows - 1)
      self.map[random_row][self.cols - 1] = 0

      max_region = [[0 for i in range(self.cols)] for j in range(self.rows)]
      max_region[random_row][self.cols - 1] = 1

    return max_region

  def regions_connected(self, regionA, regionB):
    for r in range(len(regionA)):
      for c in range(len(regionA[0])):
        if regionA[r][c] != regionB[r][c]:
          return False

        # if they share any common spaces, they're connected
        elif regionA[r][c] == 1 and regionB[r][c] == 1:
          return True

    return False

  # returns a path between all points in the list points using A*
  # if a valid path cannot be found, returns None
  def get_path(self, points, dist_map):
    num_points = len(points)
    if num_points < 2:
      raise Exception('Path needs at least two points')
    
    # check if any points aren't empty
    for point in points:
      if self.map[point[0]][point[1]] == 1:
        raise Exception('The point (%d, %d) is a wall' % (point[0], point[1]))

    overall_path = []
    for n in range(num_points - 1):
      overall_path.append(points[n])

      # generate path between this point and the next one in the list
      a_star = AStarSearch(self.map, self.infl_rad_cells)

      intermediate_path = a_star(points[n], points[n+1], dist_map)
      if not intermediate_path:
        return None
      
      # add to the overall path
      if n > 0:
        intermediate_path.pop(0)
      overall_path.extend(intermediate_path)

    return overall_path

  # robot_radius is how many cells from the center cell the robot takes up
  # robot_radius of 1 means robot takes up 3x3 cells
  # robot_radius of 2 means robot takes up 5x5 cells
  def _jmap_from_obs_map(self, robot_radius):
    jackal_map = [[0 for i in range(self.cols)] for j in range(self.rows)]

    for r in range(self.rows):
      for c in range(self.cols):
        if not self._open(r, c, robot_radius):
          jackal_map[r][c] = 1
    return jackal_map

  # checks robot_radius cells outward from (r, c)
  # returns true if robot center can be located at (r, c)
  def _open(self, row, col, robot_radius):
    for r in range(row - robot_radius, row + robot_radius + 1):
      for c in range(col - robot_radius, col + robot_radius + 1):
        if self._in_map(r, c) and self.ob_map[r][c] == 1:
          return False

    return True

  def _in_map(self, r, c):
    return 0 <= r and r < self.rows and 0 <= c and c < self.cols

  # translate the inflation radius from meters to cells
  def calc_infl_rad_cells(self):
    rad_in_cells = infl_rad * (1.0 / pgm_res)
    return round(rad_in_cells, 0)


  def get_map(self):
    return self.map


class AStarSearch:
  def __init__(self, map, infl_rad_cells):
    self.map = map
    self.map_rows = len(map)
    self.map_cols = len(map[0])
    self.infl_rad_cells = infl_rad_cells

  def __call__(self, start_coord, end_coord, dist_map):
    # limit turns to 45 degrees
    valid_moves_dict = {
      (0, 1): [(-1, 1), (0, 1), (1, 1)],
      (1, 1): [(0, 1), (1, 1), (1, 0)],
      (1, 0): [(1, 1), (1, 0), (1, -1)],
      (1, -1): [(1, 0), (1, -1), (0, -1)],
      (0, -1): [(1, -1), (0, -1), (-1, -1)],
      (-1, -1): [(0, -1), (-1, -1), (-1, 0)],
      (-1, 0): [(-1, -1), (-1, 0), (-1, 1)],
      (-1, 1): [(-1, 0), (-1, 1), (0, 1)]
    }

    # cost factor for cells within the inflation radius
    penalty_factor = 5.0

    # initialize start and end nodes
    start_node = Node(None, start_coord)
    start_node.g = start_node.h = start_node.f = 0
    end_node = Node(None, end_coord)
    end_node.g = end_node.h = end_node.f = 0

    # initialize lists to track nodes we've visited or not
    visited = []
    not_visited = []

    # add start to nodes yet to be processed
    not_visited.append(start_node)

    # while there are nodes to process
    while len(not_visited) > 0:
      
      # get lowest cost next node
      curr_node = not_visited[0]
      curr_idx = 0
      for idx, node in enumerate(not_visited):
        if node.f < curr_node.f:
          curr_node = node
          curr_idx = idx
          
      # pop this node from the unvisited list and add to visited list
      not_visited.pop(curr_idx)
      visited.append(curr_node)

      # if this node is at end of the path, return
      if curr_node == end_node:
        return self.return_path(curr_node)

      # get all valid moves (either straight or 45 degree turn)
      valid_moves = []
      if curr_node == start_node:
        # if start node, can go any direction
        valid_moves = [(0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1)]
      else:
        # otherwise, can only go straight or 45 degree turn
        moving_direction = (curr_node.r - curr_node.parent.r, curr_node.c - curr_node.parent.c)
        valid_moves = valid_moves_dict.get(moving_direction)

      # find all valid, walkable neighbors of this node
      children = []
      for move in valid_moves:

        # calculate neighbor position
        child_pos = (curr_node.r + move[0], curr_node.c + move[1])
        
        # if outside the map, not possible
        if child_pos[0] < 0 or child_pos[0] >= self.map_rows or child_pos[1] < 0 or child_pos[1] >= self.map_cols:
          continue

        # if a wall tile, not possible
        if self.map[child_pos[0]][child_pos[1]] == 1:
          continue

        # also not possible to move between diagonal walls
        if move[0] != 0 and move[1] != 0 and self.map[curr_node.r+move[0]][curr_node.c] == 1 and self.map[curr_node.r][curr_node.c+move[1]] == 1:
          continue

        # if neighbor is possible to reach, add to list of neighbors
        child_node = Node(curr_node, child_pos)
        children.append(child_node)

      # loop through all walkable neighbors of this node
      for child in children:

        # if already processed, don't use
        if child in visited:
          continue

        # calculate g value
        child_g = curr_node.g + math.sqrt((curr_node.r - child.r) ** 2 + (curr_node.c - child.c) **2)
        in_radius = dist_map[child.r][child.c] <= self.infl_rad_cells
        penalty = (penalty_factor / dist_map[child.r][child.c]) if in_radius else 0

        # check if this node is already in the unprocessed list
        child_in_openset = False
        for node in not_visited:
          if node == child:
            child_in_openset = True

            # if the node is already in the list, but with a higher cost,
            # update the cost to this new lower one
            if child_g < node.g:
              node.parent = curr_node
              node.g = child_g
              node.h = math.sqrt(((child.r - end_node.r) ** 2) + ((child.c - end_node.c) ** 2))

              # distance from start + distance to end + factor to penalize cells close to walls
              node.f = node.g + node.h + penalty

        # if child is not yet in the unprocessed list, add it
        if not child_in_openset:
          child.g = child_g
          child.h = math.sqrt(((child.r - end_node.r) ** 2) + ((child.c - end_node.c) ** 2))
          child.f = child.g + child.f + penalty
          not_visited.append(child)

  # generate the path from start to end
  def return_path(self, end_node):
    path = []
    curr_node = end_node
    while curr_node != None:
      path.append((curr_node.r, curr_node.c))
      curr_node = curr_node.parent

    path.reverse()
    return path


class Node:
  def __init__(self, parent, coord):
    self.parent = parent
    self.r = coord[0]
    self.c = coord[1]

    self.g = 0
    self.h = 0
    self.f = 0

  def __eq__(self, other):
    return self.r == other.r and self.c == other.c
 

class Display:
  def __init__(self, map_with_path, jackal_map, jackal_map_with_path, dispersion_radius):
    self.map_with_path = map_with_path
    self.jackal_map = jackal_map
    self.jackal_map_with_path = jackal_map_with_path
    self.dispersion_radius = dispersion_radius
  
    diff = DifficultyMetrics(jackal_map, path, dispersion_radius)
    self.metrics = {
      'closest_dist': diff.closest_wall(),
      'avg_vis': diff.avg_visibility(),
      'dispersion': diff.dispersion(),
      'char_dimension': diff.characteristic_dimension(),
    }

  def __call__(self):
    fig, ax = plt.subplots(3, 3) # TODO: figure out why this is causing error
    
    # map and path
    map_plot = ax[0][0].imshow(self.map_with_path, cmap='Greys', interpolation='nearest')
    map_plot.axes.get_xaxis().set_visible(False)
    map_plot.axes.get_yaxis().set_visible(False)
    ax[0][0].set_title('Map and A* path')

    # closest wall distance
    dists = self.metrics.get('closest_dist')
    dist_plot = ax[0][1].imshow(dists, cmap='RdYlGn', interpolation='nearest')
    dist_plot.axes.get_xaxis().set_visible(False)
    dist_plot.axes.get_yaxis().set_visible(False)
    ax[0][1].set_title('Distance to \nclosest obstacle')
    dist_cbar = fig.colorbar(dist_plot, ax=ax[0][1], orientation='horizontal')
    dist_cbar.ax.tick_params(labelsize='xx-small')

    # characteristic dimension
    cdr = self.metrics.get('char_dimension')
    cdr_plot = ax[0][2].imshow(cdr, cmap='binary', interpolation='nearest')
    cdr_plot.axes.get_xaxis().set_visible(False)
    cdr_plot.axes.get_yaxis().set_visible(False)
    ax[0][2].set_title('Char dimension')
    cdr_cbar = fig.colorbar(cdr_plot, ax=ax[0][2], orientation='horizontal')
    cdr_cbar.ax.tick_params(labelsize='xx-small')

    # average visibility
    avg_vis = self.metrics.get('avg_vis')
    avg_vis_plot = ax[1][0].imshow(avg_vis, cmap='RdYlGn', interpolation='nearest')
    avg_vis_plot.axes.get_xaxis().set_visible(False)
    avg_vis_plot.axes.get_yaxis().set_visible(False)
    ax[1][0].set_title('Average visibility')
    avg_vis_cbar = fig.colorbar(avg_vis_plot, ax=ax[1][0], orientation='horizontal')
    avg_vis_cbar.ax.tick_params(labelsize='xx-small')
    
    # dispersion
    dispersion = self.metrics.get('dispersion')
    disp_plot = ax[1][1].imshow(dispersion, cmap='RdYlGn', interpolation='nearest')
    disp_plot.axes.get_xaxis().set_visible(False)
    disp_plot.axes.get_yaxis().set_visible(False)
    ax[1][1].set_title('%d-square radius dispersion' % self.dispersion_radius)
    disp_cbar = fig.colorbar(disp_plot, ax=ax[1][1], orientation='horizontal')
    disp_cbar.ax.tick_params(labelsize='xx-small')

    # jackal's navigable map, low-res
    jmap_plot = ax[2][0].imshow(self.jackal_map_with_path, cmap='Greys', interpolation='nearest')
    jmap_plot.axes.get_xaxis().set_visible(False)
    jmap_plot.axes.get_yaxis().set_visible(False)
    ax[2][0].set_title('Jackal navigable map')

    plt.delaxes(ax[1][2])
    plt.axis('off')
    plt.show()


class Input:
  def __init__(self):
    self.root = tk.Tk(className='Parameters')

    tk.Label(self.root, text='Seed').grid(row=0)
    tk.Label(self.root, text='Smoothing iterations').grid(row=1)
    tk.Label(self.root, text='Fill percentage (0 to 1)').grid(row=2)
    tk.Label(self.root, text='Rows').grid(row=3, column=0)
    tk.Label(self.root, text='Cols').grid(row=3, column=2)

    self.seed = tk.Entry(self.root)
    self.seed.grid(row=0, column=1)

    self.smooth_iter = tk.Entry(self.root)
    self.smooth_iter.insert(0, '4')
    self.smooth_iter.grid(row=1, column=1)

    self.fill_pct = tk.Entry(self.root)
    self.fill_pct.insert(0,'0.35')
    self.fill_pct.grid(row=2, column=1)

    self.rows = tk.Entry(self.root)
    self.rows.insert(0, '25')
    self.rows.grid(row=3, column=1)

    self.cols = tk.Entry(self.root)
    self.cols.insert(0, '25')
    self.cols.grid(row=3, column=3)

    self.show_metrics = tk.IntVar()
    self.show_metrics.set(True)
    show_metrics_box = tk.Checkbutton(self.root, text='Show metrics', var=self.show_metrics)
    show_metrics_box.grid(row=4, column=1)

    tk.Button(self.root, text='Run', command=self.get_input).grid(row=5, column=1)

    self.root.mainloop()
  
  def get_input(self):
    self.inputs = {}

    # get seed
    if len(self.seed.get()) == 0:
      self.inputs['seed'] = hash(datetime.datetime.now())
    else:
      try:
        self.inputs['seed'] = int(self.seed.get())
      except:
        self.inputs['seed'] = hash(self.seed.get())

    # get number of smoothing iterations
    default_smooth_iter = 4
    try:
      self.inputs['smooth_iter'] = int(self.smooth_iter.get())
    except:
      self.inputs['smooth_iter'] = default_smooth_iter

    # get random fill percentage
    default_fill_pct = 0.35
    try:
      self.inputs['fill_pct'] = float(self.fill_pct.get())
    except:
      self.inputs['fill_pct'] = default_fill_pct

    # get number of rows
    default_rows = 25
    try:
      self.inputs['rows'] = int(self.rows.get())
    except:
      self.inputs['rows'] = default_rows

    # get number of columns
    default_cols = 25
    try:
      self.inputs['cols'] = int(self.cols.get())
    except:
      self.inputs['rows'] = default_cols

    # get show metrics value
    default_show_metrics = 1
    try:
      self.inputs['show_metrics'] = self.show_metrics.get()
    except:
      self.inputs['show_metrics'] = default_show_metrics
      
    self.root.destroy()
    

def main(iteration=0, seed=0, smooth_iter=4, fill_pct=.27, rows=30, cols=30, show_metrics=1):

    world_file = 'test_data/world_files/world_%d.world' % iteration
    grid_file = 'test_data/grid_files/grid_%d.npy' % iteration
    cspace_file = 'test_data/cspace_files/cspace_%d.npy' % iteration
    path_file = 'test_data/path_files/path_%d.npy' % iteration
    diff_file = 'test_data/metrics_files/metrics_%d.npy' % iteration
    pgm_file = 'test_data/map_files/map_pgm_%d.pgm' % iteration
    yaml_file = 'test_data/map_files/yaml_%d.yaml' % iteration

    """
    # get user parameters, if provided
    input_window = Input()
    input_dict = input_window.inputs
    """

    input_dict = { 'seed' : seed,
                  'smooth_iter': smooth_iter,
                  'fill_pct' : fill_pct,
                  'rows' : rows,
                  'cols' : cols,
                  'show_metrics' : show_metrics }

    # create world generator and run smoothing iterations
    print('Seed: %d' % input_dict['seed'])
    ob_map_gen = ObstacleMap(input_dict['rows'], input_dict['cols'], input_dict['fill_pct'], input_dict['seed'], input_dict['smooth_iter'])
    ob_map_gen()

    # get map from the obstacle map generator
    obstacle_map = ob_map_gen.get_map()
    
    # generate jackal's map from the obstacle map
    jmap_gen = JackalMap(obstacle_map, jackal_radius)
    start_region = jmap_gen.biggest_left_region()
    end_region = jmap_gen.biggest_right_region()

    # throw out any maps that don't have a path
    if not jmap_gen.regions_connected(start_region, end_region):
      return

    # get the final jackal map and update the obstacle map
    jackal_map = jmap_gen.get_map()

    # write map to .world file
    cyl_radius = 0.075
    contain_wall_length = 5
    writer = WorldWriter(world_file, obstacle_map, cyl_radius=cyl_radius, contain_wall_length=contain_wall_length)
    contain_wall_cylinders = writer()
    r_shift, c_shift = writer.getShifts() # TODO: change to snake case

    # Generate random start and end points for path
    left_open = []
    right_open = []
    for r in range(len(jackal_map)):
      if start_region[r][0] == 1:
        left_open.append(r)
      if end_region[r][len(jackal_map[0])-1] == 1:
        right_open.append(r)
    left_coord_r = left_open[random.randint(0, len(left_open)-1)]
    right_coord_r = right_open[random.randint(0, len(right_open)-1)]


    
    # generate path, if possible
    path = []
    diff_quant = DifficultyMetrics(jackal_map, path, radius=3)
    dist_map = diff_quant.closest_wall()
    print('Points: (%d, 0), (%d, %d)' % (left_coord_r, right_coord_r, len(jackal_map[0])-1))
    path = jmap_gen.get_path([(left_coord_r, 0), (right_coord_r, len(jackal_map[0])-1)], dist_map)

    if not path:
      print('path not found')
      return # path not found, don't use this world

    print('Found path!')


    # print start and end points in gazebo coords
    start_r = r_shift + left_coord_r * cyl_radius * 2
    start_c = c_shift
    end_r = r_shift + right_coord_r * cyl_radius * 2
    end_c = len(obstacle_map[0]) * cyl_radius * 2 + c_shift
    print('Start: (%f, %f) to Goal: (%f, %f)' % (start_r, start_c, end_r, end_c))

    # put paths into matrices to display them
    obstacle_map_with_path = [[obstacle_map[j][i] for i in range(len(obstacle_map[0]))] for j in range(len(obstacle_map))]
    jackal_map_with_path = [[jackal_map[j][i] for i in range(len(jackal_map[0]))] for j in range(len(jackal_map))]
    for r, c in path:
      # update jackal-space path display
      jackal_map_with_path[r][c] = 0.35

      # update obstacle-space path display
      for r_kernel in range(r - jackal_radius, r + jackal_radius + 1):
        for c_kernel in range(c - jackal_radius, c + jackal_radius + 1):
          if 0 <= r_kernel and r_kernel < rows and 0 <= c_kernel and c_kernel < cols:
            obstacle_map_with_path[r_kernel][c_kernel] = 0.35


    jackal_map_with_path[left_coord_r][0] = 0.65
    jackal_map_with_path[right_coord_r][len(jackal_map[0])-1] = 0.65
    obstacle_map_with_path[left_coord_r][0] = 0.65
    obstacle_map_with_path[right_coord_r][len(obstacle_map[0])-1] = 0.65
    
    # save occupancy grid
    grid_arr = np.asarray(obstacle_map)
    np.save(grid_file, grid_arr)

    # save cspace
    cspace_grid = np.asarray(jackal_map)
    np.save(cspace_file, cspace_grid)

    # save path
    path_arr = np.asarray(path)
    np.save(path_file, path_arr)

    # save metrics
    diff = DifficultyMetrics(jackal_map, path, radius=3)
    metrics_arr = np.asarray(diff.avg_all_metrics())
    print(metrics_arr)
    np.save(diff_file, metrics_arr)


    # write the map to a pgm file for navigation
    pgm_writer = PGMWriter(obstacle_map, contain_wall_cylinders, pgm_file)
    pgm_writer()

    # write map metadata to yaml file
    yw = YamlWriter(yaml_file, iteration)
    yw.write()
    

    """
    # display world and heatmap of distances
    if input_dict['show_metrics']:
      display = Display(obstacle_map_with_path, jackal_map, jackal_map_with_path, dispersion_radius=3)
      display()
    
    # only show the map itself
    else:
      plt.imshow(obstacle_map_with_path, cmap='Greys', interpolation='nearest')
      plt.show()
    """
    
        
    return True # path found
    

if __name__ == "__main__":
    main(iteration = -1, seed=2422863611227240384, fill_pct=0.2, smooth_iter=4)
