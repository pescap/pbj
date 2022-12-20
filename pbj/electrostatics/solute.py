import re
import bempp.api
import os
import numpy as np
import time
from numba import jit
import pbj.mesh.mesh_tools as mesh_tools
import pbj.mesh.charge_tools as charge_tools
import pbj.electrostatics.pb_formulation.formulations as pb_formulations
import pbj.electrostatics.utils as utils


class Solute:
    """The basic Solute object
    This object holds all the solute information and allows for an easy way to hold the data
    """

    def __init__(
        self,
        solute_file_path,
        external_mesh_file=None,
        save_mesh_build_files=False,
        mesh_build_files_dir="mesh_files/",
        mesh_density=2.0,
        nanoshaper_grid_scale=None,
        mesh_probe_radius=1.4,
        mesh_generator="nanoshaper",
        print_times=False,
        force_field="amber",
        formulation="direct",
        radius_keyword="solute",
        solute_radius_type="PB"
    ):

        if not os.path.isfile(solute_file_path):
            print("file does not exist -> Cannot start")
            return

        if force_field == "amoeba" and formulation != "direct":
            print("AMOEBA force field is only available with the direct formulation -> Changing to direct")
            formulation = "direct"

        self._pb_formulation = formulation

        self.formulation_object = getattr(pb_formulations, self.pb_formulation, None)
        if self.formulation_object is None:
            raise ValueError("Unrecognised formulation type %s" % self.pb_formulation)

        self.force_field = force_field

        self.radius_keyword = radius_keyword
        self.solute_radius_type = solute_radius_type

        self.save_mesh_build_files = save_mesh_build_files
        self.mesh_build_files_dir = os.path.abspath(mesh_build_files_dir)

        if nanoshaper_grid_scale is not None:
            if mesh_generator == "nanoshaper":
                print("Using specified grid_scale.")
                self.nanoshaper_grid_scale = nanoshaper_grid_scale
            else:
                print(
                    "Ignoring specified grid scale as mesh_generator is not specified as nanoshaper."
                )
                self.mesh_density = mesh_density
        else:
            self.mesh_density = mesh_density
            if mesh_generator == "nanoshaper":
                self.nanoshaper_grid_scale = (
                    mesh_tools.density_to_nanoshaper_grid_scale_conversion(
                        self.mesh_density
                    )
                )
        self.mesh_probe_radius = mesh_probe_radius
        self.mesh_generator = mesh_generator

        self.print_times = print_times

        file_extension = solute_file_path.split(".")[-1]
        if file_extension == "pdb":
            self.imported_file_type = "pdb"
            self.pdb_path = solute_file_path
            self.solute_name = get_name_from_pdb(self.pdb_path)

        elif file_extension == "pqr":
            self.imported_file_type = "pqr"
            self.pqr_path = solute_file_path
            self.solute_name = os.path.split(solute_file_path.split(".")[-2])[-1]

        elif file_extension == "xyz":
            self.imported_file_type = "xyz"
            self.xyz_path = solute_file_path
            self.solute_name = os.path.split(solute_file_path.split(".")[-2])[-1]

        else:
            print("File is not pdb, pqr, or Tinker xyz -> Cannot start")

        if external_mesh_file is not None:
            filename, file_extension = os.path.splitext(external_mesh_file)
            if file_extension == "":  # Assume use of vert and face
                self.external_mesh_face_path = external_mesh_file + ".face"
                self.external_mesh_vert_path = external_mesh_file + ".vert"
                self.mesh = mesh_tools.import_msms_mesh(
                    self.external_mesh_face_path, self.external_mesh_vert_path
                )

            else:  # Assume use of file that can be directly imported into bempp
                self.external_mesh_file_path = external_mesh_file
                self.mesh = bempp.api.import_grid(self.external_mesh_file_path)

            if force_field == "amoeba":
                (
                    self.x_q, 
                    self.q, 
                    self.d,
                    self.Q, 
                    self.alpha, 
                    self.r_q,
                    self.mass, 
                    self.polar_group, 
                    self.thole, 
                    self.connections_12, 
                    self.connections_13, 
                    self.pointer_connections_12, 
                    self.pointer_connections_13, 
                    self.p12scale, 
                    self.p13scale, 
                ) = charge_tools.load_tinker_multipoles_to_solute(self)

                self.d_induced = np.zeros_like(self.d)
                self.d_induced_prev = np.zeros_like(self.d)
            else: 
                self.q, self.x_q, self.r_q = charge_tools.load_charges_to_solute(
                    self
                )  # Import charges from given file

        else:  # Generate mesh from given pdb or pqr, and import charges at the same time

            if force_field == "amoeba":
                (
                    self.mesh,
                    self.x_q, 
                    self.q, 
                    self.d, 
                    self.Q, 
                    self.alpha, 
                    self.r_q,
                    self.mass, 
                    self.polar_group, 
                    self.thole, 
                    self.connections_12, 
                    self.connections_13, 
                    self.pointer_connections_12, 
                    self.pointer_connections_13, 
                    self.p12scale, 
                    self.p13scale, 
                ) = charge_tools.generate_msms_mesh_import_tinker_multipoles(self)

                self.d_induced = np.zeros_like(self.d)
                self.d_induced_prev = np.zeros_like(self.d)


            else:
                (
                    self.mesh,
                    self.q,
                    self.x_q,
                    self.r_q,
                ) = charge_tools.generate_msms_mesh_import_charges(self)

        self.ep_in = 4.0
        self.ep_ex = 80.0
        self.kappa = 0.125

        self.pb_formulation_alpha = 1.0  # np.nan
        self.pb_formulation_beta = self.ep_ex / self.ep_in  # np.nan

        self.pb_formulation_stern_width = 2.0
        self.stern_object = None

        self.slic_alpha = 0.5
        self.slic_beta = -60
        self.slic_gamma = -0.5

        self.slic_max_iterations = 20
        self.slic_tolerance = 1e-5

        self.pb_formulation_preconditioning = True
        self.pb_formulation_preconditioning_type = "mass_matrix"

        self.discrete_form_type = "weak"

        self.gmres_tolerance = 1e-5
        self.gmres_restart = 1000
        self.gmres_max_iterations = 1000

        self.operator_assembler = "dense"
        self.rhs_constructor = "numpy"

        self.matrices = dict()
        self.rhs = dict()
        self.results = dict()
        self.timings = dict()

        # Setup Dirichlet and Neumann spaces to use, save these as object vars
        dirichl_space = bempp.api.function_space(self.mesh, "P", 1)
        # neumann_space = bempp.api.function_space(self.mesh, "P", 1)
        neumann_space = dirichl_space
        self.dirichl_space = dirichl_space
        self.neumann_space = neumann_space

    @property
    def pb_formulation(self):
        return self._pb_formulation

    @pb_formulation.setter
    def pb_formulation(self, value):
        self._pb_formulation = value
        self.formulation_object = getattr(pb_formulations, self.pb_formulation, None)
        if "preconditioning_matrix_gmres" not in self.matrices: # might already exist if just regenerating RHS
            self.matrices["preconditioning_matrix_gmres"] = None

        if self.formulation_object is None:
            raise ValueError("Unrecognised formulation type %s" % self.pb_formulation)

    @property
    def stern_mesh_density(self):
        return self._stern_mesh_density

    @stern_mesh_density.setter
    def stern_mesh_density(self, value):
        self._stern_mesh_density = value
        pb_formulations.direct_stern.create_stern_mesh(self)

    def display_available_formulations(self):
        from inspect import getmembers, ismodule

        print("Current formulation: " + self.pb_formulation)
        print("List of available formulations:")
        available = getmembers(pb_formulations, ismodule)
        for element in available:
            if element[0] == "common":
                available.remove(element)
        for name, object_address in available:
            print(name)

    def display_available_preconditioners(self):
        from inspect import getmembers, isfunction

        print(
            "List of preconditioners available for the current formulation ("
            + self.pb_formulation
            + "):"
        )
        for name, object_address in getmembers(self.formulation_object, isfunction):
            if name.endswith("preconditioner"):
                name_removed = name[:-15]
                print(name_removed)

    def initialise_matrices(self):
        start_time = time.time()  # Start the timing for the matrix construction
        # Construct matrices based on the desired formulation
        # Verify if parameters are already set and save A matrix
        if self.formulation_object.verify_parameters(self):
            self.formulation_object.lhs(self)
        self.timings["time_matrix_initialisation"] = time.time() - start_time

    def assemble_matrices(self): # not being used, as this is done in apply_preconditioning
        start_assembly = time.time()
        self.matrices["A"].weak_form()
        self.timings["time_matrix_assembly"] = time.time() - start_assembly

    def initialise_rhs(self):
        start_rhs = time.time()
        # Verify if parameters are already set and then save RHS
        if self.formulation_object.verify_parameters(self):
            self.formulation_object.rhs(self)
        self.timings["time_rhs_initialisation"] = time.time() - start_rhs
        
    def initialise_rhs_induced_dipole(self):
        start_rhs = time.time()
        # Verify if parameters are already set and then save RHS
        if self.formulation_object.verify_parameters(self):
            self.formulation_object.rhs_induced_dipole(self)
        self.timings["time_rhs_initialisation"] = time.time() - start_rhs

    def apply_preconditioning(self):
        preconditioning_start_time = time.time()
        if self.pb_formulation_preconditioning:
            precon_str = self.pb_formulation_preconditioning_type + "_preconditioner"
            preconditioning_object = getattr(self.formulation_object, precon_str, None)
            if preconditioning_object is not None:
                preconditioning_object(self)
            else:
                raise ValueError(
                    "Unrecognised preconditioning type %s for current formulation type %s"
                    % (self.pb_formulation_preconditioning_type, self.pb_formulation)
                )
        else:
            self.matrices["A_final"] = self.matrices["A"]
            self.rhs["rhs_final"] = [rhs for key, rhs in sorted(self.rhs.items())][
                : len(self.matrices["A"].domain_spaces)
            ]

            self.matrices["A_discrete"] = utils.matrix_to_discrete_form(
                self.matrices["A_final"], "weak"
            )
            self.rhs["rhs_discrete"] = utils.rhs_to_discrete_form(
                self.rhs["rhs_final"], "weak", self.matrices["A"]
            )

        self.timings["time_preconditioning"] = time.time() - preconditioning_start_time
        
    def apply_preconditioning_rhs(self):
        preconditioning_start_time = time.time()
        if self.pb_formulation_preconditioning and self.matrices["preconditioning_matrix_gmres"] is None:
            precon_str = self.pb_formulation_preconditioning_type + "_preconditioner_rhs"
            preconditioning_object = getattr(self.formulation_object, precon_str, None)
            if preconditioning_object is not None:
                preconditioning_object(self)
            else:
                raise ValueError(
                    "Unrecognised preconditioning type %s for current formulation type %s"
                    % (self.pb_formulation_preconditioning_type, self.pb_formulation)
                )
        else:
            self.rhs["rhs_final"] = [rhs for key, rhs in sorted(self.rhs.items())][
                : len(self.matrices["A"].domain_spaces)
            ]
            self.rhs["rhs_discrete"] = utils.rhs_to_discrete_form(
                self.rhs["rhs_final"], "weak", self.matrices["A"]
            )

        self.timings["time_preconditioning"] = time.time() - preconditioning_start_time
        

    def calculate_potential(self, rerun_all=False): # I think this calculate_potential is not working correctly
        if self.formulation_object.verify_parameters(self):
            self.formulation_object.calculate_potential(self, rerun_all)

    def calculate_solvation_energy(self):

        if "phi" not in self.results:
            print("Please compute surface potential first with simulation.calculate_potentials()")
            return

        start_time = time.time()

        solution_dirichl = self.results["phi"]
        solution_neumann = self.results["d_phi"]

        from bempp.api.operators.potential.laplace import single_layer, double_layer

        slp_q = single_layer(self.neumann_space, self.x_q.transpose())
        dlp_q = double_layer(self.dirichl_space, self.x_q.transpose())
        phi_q = slp_q * solution_neumann - dlp_q * solution_dirichl

        self.results["phir_charges"] = phi_q

        # total solvation energy applying constant to get units [kcal/mol]
        total_energy = 2 * np.pi * 332.064 * np.sum(self.q * phi_q).real
        self.results["solvation_energy"] = total_energy
        self.timings["time_calc_energy"] = time.time() - start_time

        if self.print_times:
            print(
                "It took ",
                self.timings["time_calc_energy"],
                " seconds to compute the solvation energy",
            )

    def calculate_solvation_energy_polarizable(self):

        start_time = time.time()

        if "phi" not in self.results:
            print("Please compute surface potential first with simulation.calculate_potentials()")
            return

        q = self.q
        d = self.d
        Q = self.Q

        solution_dirichl = self.results["phi"]
        solution_neumann = self.results["d_phi"]

        from bempp.api.operators.potential.laplace import single_layer, double_layer

        slp_q = single_layer(self.neumann_space, self.x_q.transpose())
        dlp_q = double_layer(self.dirichl_space, self.x_q.transpose())
        phi_q = slp_q * solution_neumann - dlp_q * solution_dirichl

        self.results["phir_charges"] = phi_q[0,:]

        if "gradphir_charges" not in self.results:
            self.calculate_gradient_field()

        self.calculate_gradgradient_field()

        # total solvation energy applying constant to get units [kcal/mol]
        q_aux = 0
        d_aux = 0
        Q_aux = 0
        
        dphi_q = self.results["gradphir_charges"]
        ddphi_q = self.results["gradgradphir_charges"]
        
        for i in range(len(q)):
            q_aux += q[i]*phi_q[0,i]
            
            for j in range(3):
                d_aux += d[i,j]*dphi_q[i,j]
                
                for k in range(3):
                    Q_aux += Q[i,j,k]*ddphi_q[i,j,k]/6.

        solvent_energy = 2 * np.pi * 332.064 * (q_aux + d_aux + Q_aux)
        coulomb_energy_dissolved = self.calculate_coulomb_energy_multipole(state="dissolved")

        
        self.calculate_induced_dipole_vacuum() 
        coulomb_energy_vacuum = self.calculate_coulomb_energy_multipole(state="vacuum")

        self.results["solvation_energy"] = solvent_energy + coulomb_energy_dissolved - coulomb_energy_vacuum
        self.timings["time_calc_energy"] = time.time() - start_time
        
        if self.print_times:
            print(
                "It took ",
                self.timings["time_calc_energy"],
                " seconds to compute the solvation energy",
            )

    def calculate_coulomb_energy_multipole(self, state):
        """
        Calculates the Coulomb energy 

        state: (string) dissolved or vacuum, to choose which induced dipole to use
        """
    
        N = len(self.x_q)
        
        q = self.q
        d = self.d
        Q = self.Q

        #phi, dphi and ddphi from permanent multipoles
        phi_perm   = self.calculate_coulomb_phi_multipole()
        flag_polar_group = False
        dphi_perm  = self.calculate_coulomb_dphi_multipole(flag_polar_group) # Recalculate for energy as flag = False
        ddphi_perm = self.calculate_coulomb_ddphi_multipole()
        
        self.results["phi_perm_multipoles"]         = phi_perm
        self.results["gradphi_perm_multipoles"]     = dphi_perm
        self.results["gradgradphi_perm_multipoles"] = ddphi_perm
       
        
        #phi, dphi and ddphi from induced dipoles
        phi_thole = self.calculate_coulomb_phi_multipole_Thole(state)
        dphi_thole = self.calculate_coulomb_dphi_multipole_Thole(state)
        ddphi_thole = self.calculate_coulomb_ddphi_multipole_Thole(state)
        
        self.results["phi_induced_dipole_"+state]         = phi_thole
        self.results["gradphi_induced_dipole_"+state]     = dphi_thole
        self.results["gradgradphi_induced_dipole_"+state] = ddphi_thole
        
        phi   = phi_perm  + phi_thole
        dphi  = dphi_perm + dphi_thole
        ddphi = ddphi_perm + ddphi_thole
        
        point_energy = q[:]*phi[:] + np.sum(d[:] * dphi[:], axis = 1) + (np.sum(np.sum(Q[:]*ddphi[:], axis = 1), axis = 1))/6.

        cal2J = 4.184
        ep_vacc = 8.854187818e-12
        qe = 1.60217646e-19
        Na = 6.0221415e+23
        C0 = qe**2*Na*1e-3*1e10/(cal2J*ep_vacc)
        
        coulomb_energy = sum(point_energy) * 0.5*C0/(4*np.pi*self.ep_in) 
        
        return coulomb_energy


    def calculate_gradient_field(self, h=0.001):

        """
        Compute the first derivate of potential due to solvent
        in the position of the points
        """

        if "phi" not in self.results:
            print("Please compute surface potential first with simulation.calculate_potentials()")
            return


        start_time = time.time()

        solution_dirichl = self.results["phi"]
        solution_neumann = self.results["d_phi"]

        dphidr = np.zeros([len(self.x_q), 3])
        dist = np.diag([h, h, h])  # matriz 3x3 diagonal de h

        # x axis derivate
        dx = np.concatenate(
            (self.x_q[:] + dist[0], self.x_q[:] - dist[0])
        )  # vector x+h y luego x-h
        slpo = bempp.api.operators.potential.laplace.single_layer(
            self.neumann_space, dx.transpose()
        )
        dlpo = bempp.api.operators.potential.laplace.double_layer(
            self.dirichl_space, dx.transpose()
        )
        phi = slpo.evaluate(solution_neumann) - dlpo.evaluate(solution_dirichl)
        dphidx = 0.5 * (phi[0, : len(self.x_q)] - phi[0, len(self.x_q) :]) / h
        dphidr[:, 0] = dphidx

        # y axis derivate
        dy = np.concatenate((self.x_q[:] + dist[1], self.x_q[:] - dist[1]))
        slpo = bempp.api.operators.potential.laplace.single_layer(
            self.neumann_space, dy.transpose()
        )
        dlpo = bempp.api.operators.potential.laplace.double_layer(
            self.dirichl_space, dy.transpose()
        )
        phi = slpo.evaluate(solution_neumann) - dlpo.evaluate(solution_dirichl)
        dphidy = 0.5 * (phi[0, : len(self.x_q)] - phi[0, len(self.x_q) :]) / h
        dphidr[:, 1] = dphidy

        # z axis derivate
        dz = np.concatenate((self.x_q[:] + dist[2], self.x_q[:] - dist[2]))
        slpo = bempp.api.operators.potential.laplace.single_layer(
            self.neumann_space, dz.transpose()
        )
        dlpo = bempp.api.operators.potential.laplace.double_layer(
            self.dirichl_space, dz.transpose()
        )
        phi = slpo.evaluate(solution_neumann) - dlpo.evaluate(solution_dirichl)
        dphidz = 0.5 * (phi[0, : len(self.x_q)] - phi[0, len(self.x_q) :]) / h
        dphidr[:, 2] = dphidz

        self.results["gradphir_charges"] = dphidr
        self.timings["time_calc_gradient_field"] = time.time() - start_time

        if self.print_times:
            print(
                "It took ",
                self.timings["time_calc_gradient_field"],
                " seconds to compute the gradient field on solute charges",
            )
        return None

    def calculate_gradgradient_field(self, h=0.001):

        """
        Compute the second derivate of potential due to solvent
        in the position of the points
        xq: Array size (Nx3) whit positions to calculate the derivate.
        h: Float number, distance for the central difference.
        Return:
        ddphi: Second derivate of the potential in the positions of points.
        """

        if "phi" not in self.results:
            print("Please compute surface potential first with simulation.calculate_potentials()")
            return

        start_time = time.time()

        x_q = self.x_q
        neumann_space = self.neumann_space
        dirichl_space = self.dirichl_space
        solution_neumann = self.results["d_phi"]
        solution_dirichl = self.results["phi"]

        ddphi = np.zeros((len(x_q),3,3))
        dist = np.array(([h,0,0],[0,h,0],[0,0,h]))
        for i in range(3):
            for j in np.where(np.array([0, 1, 2]) >= i)[0]:
                if i==j:
                    dp = np.concatenate((x_q[:] + dist[i], x_q[:], x_q[:] - dist[i]))
                    slpo = bempp.api.operators.potential.laplace.single_layer(neumann_space, dp.transpose())
                    dlpo = bempp.api.operators.potential.laplace.double_layer(dirichl_space, dp.transpose())
                    phi = slpo.evaluate(solution_neumann) - dlpo.evaluate(solution_dirichl)
                    ddphi[:,i,j] = (phi[0,:len(x_q)] - 2*phi[0,len(x_q):2*len(x_q)] + phi[0, 2*len(x_q):])/(h**2)

                else:
                    dp = np.concatenate((x_q[:] + dist[i] + dist[j], x_q[:] - dist[i] - dist[j], x_q[:] + \
                                         dist[i] - dist[j], x_q[:] - dist[i] + dist[j]))
                    slpo = bempp.api.operators.potential.laplace.single_layer(neumann_space, dp.transpose())
                    dlpo = bempp.api.operators.potential.laplace.double_layer(dirichl_space, dp.transpose())
                    phi = slpo.evaluate(solution_neumann) - dlpo.evaluate(solution_dirichl)
                    ddphi[:,i,j] = (phi[0,:len(x_q)] + phi[0,len(x_q):2*len(x_q)] - \
                                    phi[0, 2*len(x_q):3*len(x_q)] - phi[0, 3*len(x_q):])/(4*h**2)
                    ddphi[:,j,i] = (phi[0,:len(x_q)] + phi[0,len(x_q):2*len(x_q)] - \
                                    phi[0, 2*len(x_q):3*len(x_q)] - phi[0, 3*len(x_q):])/(4*h**2)

            self.results["gradgradphir_charges"] = ddphi
            self.timings["time_calc_gradgrad_field"] = time.time() - start_time

            if self.print_times:
                print(
                    "It took ",
                    self.timings["time_calc_gradgrad_field"],
                    " seconds to compute the gradient of the gradient field on solute charges",
                )


    def calculate_charges_forces(self, h=0.001):

        if "phi" not in self.results:
            print("Please compute surface potential first with simulation.calculate_potentials()")
            return

        if "gradphir_charges" not in self.results:
            # If gradient field has not been calculated, calculate it now
            self.calculate_gradient_field(h=h)

        start_time = time.time()

        dphidr = self.results["gradphir_charges"]

        convert_to_kcalmolA = 4 * np.pi * 332.0636817823836

        f_reac = convert_to_kcalmolA * -np.transpose(np.transpose(dphidr) * self.q)
        f_reactotal = np.sum(f_reac, axis=0)

        self.results["f_qf_charges"] = f_reac
        self.results["f_qf"] = f_reactotal
        self.timings["time_calc_solute_force"] = time.time() - start_time

        if self.print_times:
            print(
                "It took ",
                self.timings["time_calc_solute_force"],
                " seconds to compute the force on solute charges",
            )

        return None

    def calculate_boundary_forces(self, fdb_approx=False):

        if "phi" not in self.results:
            print("Please compute surface potential first with simulation.calculate_potentials()")
            return

        start_time = time.time()

        phi = self.results["phi"].evaluate_on_element_centers()
        d_phi = self.results["d_phi"].evaluate_on_element_centers()

        convert_to_kcalmolA = 4 * np.pi * 332.0636817823836
        dS = np.transpose(np.transpose(self.mesh.normals) * self.mesh.volumes)

        if fdb_approx :
            # Dielectric boundary force
            f_db = (
                -0.5
                * convert_to_kcalmolA
                * (self.ep_ex - self.ep_in)
                * (self.ep_in / self.ep_ex)
                * np.sum(np.transpose(np.transpose(dS) * d_phi[0] ** 2), axis=0)
            )

        else:
            N_elements = self.mesh.number_of_elements
            phi_vertex = self.results["phi"].coefficients
            ep_hat =  self.ep_in/self.ep_ex
            dphi_centers = ep_hat * self.results["d_phi"].evaluate_on_element_centers()[0]
            f_db = np.zeros(3)
            convert_to_kcalmolA = 4 * np.pi * 332.0636817823836
            for i in range(N_elements):
                eps = self.mesh.normals[i]
                    
                # get vertex indices adyacent to a triangular element
                v1_index = self.mesh.elements[0,i]
                v2_index = self.mesh.elements[1,i]
                v3_index = self.mesh.elements[2,i]
                
                # get vertex coordinates from vertex indices
                v1 = self.mesh.vertices[:,v1_index]
                v2 = self.mesh.vertices[:,v2_index]
                v3 = self.mesh.vertices[:,v3_index]
                
                v21 = v2 - v1
                v31 = v3 - v1
                
                v21_norm = np.linalg.norm(v21)
                v31_norm = np.linalg.norm(v31)
                
                phi_1 = phi_vertex[v1_index]
                phi_2 = phi_vertex[v2_index]
                phi_3 = phi_vertex[v3_index]
                
                alpha = np.arccos(np.dot(v21,v31)/(v21_norm*v31_norm))
                
                a = (phi_2 - phi_1)/v21_norm
                b = (phi_3 - phi_1)/(v31_norm*np.sin(alpha)) - (phi_2-phi_1)/(v21_norm*np.tan(alpha))
                
                eta = v21/v21_norm
                tau = np.cross(eps,eta)
                
                E_eps = -dphi_centers[i]
                E_eta = -a
                E_tau = -b
                

                F = ((1/ep_hat)*E_eps*E_eps + E_eta*E_eta + E_tau*E_tau)
                F *= -0.5*(self.ep_ex - self.ep_in)*self.mesh.normals[i] * self.mesh.volumes[i]
                
                f_db += convert_to_kcalmolA * F    

        # Ionic boundary force
        f_ib = (
            -0.5
            * convert_to_kcalmolA
            * (self.ep_ex)
            * (self.kappa**2)
            * np.sum(np.transpose(np.transpose(dS) * phi[0] ** 2), axis=0)
        )

        self.results["f_db"] = f_db
        self.results["f_ib"] = f_ib
        self.timings["time_calc_boundary_force"] = time.time() - start_time

        if self.print_times:
            print(
                "It took ",
                self.timings["time_calc_boundary_force"],
                " seconds to compute the boundary forces",
            )

    def calculate_solvation_forces(self, h=0.001, force_formulation='maxwell_tensor', fdb_approx=False):

        if "phi" not in self.results:
            print("Please compute surface potential first with simulation.calculate_potentials()")
            return

        if force_formulation == 'energy_functional':
            if "f_qf" not in self.results:
                self.calculate_gradient_field(h=h)
                self.calculate_charges_forces()

            self.calculate_boundary_forces(fdb_approx=fdb_approx)

            start_time = time.time()

            f_solv = np.zeros([3])
            f_qf = self.results["f_qf"]
            f_db = self.results["f_db"]
            f_ib = self.results["f_ib"]
            f_solv = f_qf + f_db + f_ib

            self.results["f_solv"] = f_solv
            self.timings["time_calc_solvation_force"] = (
                time.time()
                - start_time
                + self.timings["time_calc_boundary_force"]
                + self.timings["time_calc_solute_force"]
                + self.timings["time_calc_gradient_field"]
            )
            if self.print_times:
                print(
                    "It took ",
                    self.timings["time_calc_solvation_force"],
                    " seconds to compute the solvation forces with ",
                    force_formulation,
                    " formulation"
                )

        elif force_formulation == 'maxwell_tensor':

            if "f_ib" not in self.results:
                self.calculate_boundary_forces()

            start_time = time.time()


            N_elements = self.mesh.number_of_elements
            P_normal = np.zeros([N_elements])
            phi_vertex = self.results["phi"].coefficients
            ep_hat =  self.ep_in/self.ep_ex
            dphi_centers = ep_hat * self.results["d_phi"].evaluate_on_element_centers()[0]
            total_force = np.zeros(3)
            convert_to_kcalmolA = 4 * np.pi * 332.0636817823836

            for i in range(N_elements):
                eps = self.mesh.normals[i]
                
                # get vertex indices adyacent to a triangular element
                v1_index = self.mesh.elements[0,i]
                v2_index = self.mesh.elements[1,i]
                v3_index = self.mesh.elements[2,i]
                
                # get vertex coordinates from vertex indices
                v1 = self.mesh.vertices[:,v1_index]
                v2 = self.mesh.vertices[:,v2_index]
                v3 = self.mesh.vertices[:,v3_index]
                
                v21 = v2 - v1
                v31 = v3 - v1
                
                v21_norm = np.linalg.norm(v21)
                v31_norm = np.linalg.norm(v31)
                
                phi_1 = phi_vertex[v1_index]
                phi_2 = phi_vertex[v2_index]
                phi_3 = phi_vertex[v3_index]
                
                alpha = np.arccos(np.dot(v21,v31)/(v21_norm*v31_norm))
                
                a = (phi_2 - phi_1)/v21_norm
                b = (phi_3 - phi_1)/(v31_norm*np.sin(alpha)) - (phi_2-phi_1)/(v21_norm*np.tan(alpha))
                
                eta = v21/v21_norm
                tau = np.cross(eps,eta)
                
                E_eps = -dphi_centers[i]
                E_eta = -a
                E_tau = -b
                
                E_norm = np.sqrt(E_eps*E_eps + E_eta*E_eta + E_tau*E_tau)
                
                F = (E_eps*E_eps - 0.5*E_norm*E_norm)*eps + E_eps*E_eta*eta + E_eps*E_tau*tau 
                
                F *= self.ep_ex
                total_force += F * self.mesh.volumes[i]   
                P_normal[i] = np.sqrt(np.dot(F * self.mesh.volumes[i], F * self.mesh.volumes[i]))
                
            self.results["P_normal"] = convert_to_kcalmolA * P_normal
            self.results["f_solv"] = convert_to_kcalmolA * total_force + self.results["f_ib"]
            self.timings["time_calc_solvation_force"] = (
                time.time()
                - start_time
            )
            if self.print_times:
                print(
                    "It took ",
                    self.timings["time_calc_solvation_force"],
                    " seconds to compute the solvation forces with ",
                    force_formulation,
                    " formulation"
                )

        else:
            raise ValueError('Formulation have to be "maxwell_tensor" or "energy_functional"')


    def calculate_induced_dipole_dissolved(self):
        
        N = len(self.x_q)
        
        p12scale_temp = self.p12scale
        p13scale_temp = self.p13scale
        
        u12scale = 1.0
        u13scale = 1.0
        
        self.p12scale = u12scale
        self.p13scale = u13scale  # scaling for induced dipole calculation

        alphaxx = self.alpha[:,0,0]
        
        if "d_phi_coulomb_multipole" not in self.results:
            dphi_perm = self.calculate_coulomb_dphi_multipole()
            self.results["d_phi_coulomb_multipole"] = dphi_perm
        
        dphi_Thole = self.calculate_coulomb_dphi_multipole_Thole(state="dissolved")    
        
        
        self.p12scale = p12scale_temp
        self.p13scale = p13scale_temp
        

        dphi_coul = self.results["d_phi_coulomb_multipole"] + dphi_Thole
        
        dphi_reac = self.results["gradphir_charges"] 
        
        d_induced = self.results["induced_dipole"]

        SOR = self.SOR
        for i in range(N):
            
            E_total = (dphi_coul[i]/self.ep_in + 4*np.pi*dphi_reac[i])*-1
            d_induced[i] = d_induced[i]*(1 - SOR) + np.dot(alphaxx[i], E_total)*SOR
            
        self.d_induced[:] = d_induced[:]
        
        self.results["induced_dipole"] = d_induced


    def calculate_induced_dipole_vacuum(self):
        
        N = len(self.x_q)
        
        
        u12scale = 1.0
        u13scale = 1.0
       

        alphaxx = self.alpha[:,0,0]
        
        
        if "d_phi_coulomb_multipole" not in self.results:
            dphi_perm = self.calculate_coulomb_dphi_multipole()
            self.results["d_phi_coulomb_multipole"] = dphi_perm
        
        d_induced = np.zeros_like(self.d)
        self.results["induced_dipole_vacuum"] = d_induced

        self.results["dipole_iter_count_vacuum"] = 0 

        induced_dipole_residual = 1.
        dipole_iter_count_vacuum = 1

        while induced_dipole_residual > self.induced_dipole_iter_tol:
    
            p12scale_temp = self.p12scale
            p13scale_temp = self.p13scale
        
            self.p12scale = u12scale
            self.p13scale = u13scale  # scaling for induced dipole calculation
    
            dphi_Thole = self.calculate_coulomb_dphi_multipole_Thole(state = "vacuum")
              
            self.p12scale = p12scale_temp
            self.p13scale = p13scale_temp
            
            dphi_coul = self.results["d_phi_coulomb_multipole"] + dphi_Thole
            
            d_induced_prev = d_induced.copy()

            SOR = self.SOR
            for i in range(N):
                
                E_total = (dphi_coul[i]/self.ep_in)*-1
                d_induced[i] = d_induced[i]*(1 - SOR) + np.dot(alphaxx[i], E_total)*SOR
        
            induced_dipole_residual = np.max(np.sqrt(np.sum(
                                (np.linalg.norm(d_induced_prev-d_induced,axis=1))**2)/len(d_induced)
                            )
                        )

            print("Vacuum induced dipole iteration %i -> residual: %s"%(
                        dipole_iter_count_vacuum, induced_dipole_residual
                        )
                    )

            dipole_iter_count_vacuum += 1
        
        self.results["induced_dipole_vacuum"] = d_induced

    def calculate_coulomb_phi_multipole(self):
        """
        Calculate the potential due to the permanent multipoles
        """
        xq = self.x_q
        q = self.q
        d = self.d
        Q = self.Q

        phi = self._calculate_coulomb_phi_multipole(xq, q, d, Q)

        return phi 

    @staticmethod
    @jit(nopython=True)
    def _calculate_coulomb_phi_multipole(xq, q, d, Q):
        """
        Performs calculation of potencial due to permanent multipoles with jit
        """
        N = len(xq)
        eps = 1e-15
        phi = np.zeros(N)
        
        T2 = np.zeros((N-1,3,3))
        
        for i in range(N):
            
            Ri = xq[i] - xq
            Rnorm = np.sqrt(np.sum((Ri*Ri), axis = 1) + eps*eps)
            
            Ri = np.delete(Ri, (3*i, 3*i+1, 3*i+2)).reshape((N-1,3))
            Rnorm = np.delete(Rnorm, i)
            q_temp = np.delete(q, i)
            d_temp = np.delete(d, (3*i, 3*i+1, 3*i+2)).reshape((N-1,3))
            Q_temp = np.delete(Q, (9*i, 9*i+1, 9*i+2, 9*i+3, 9*i+4, 9*i+5, 9*i+6, 9*i+7, 9*i+8)).reshape((N-1,3,3))
            
            T0 = 1./Rnorm[:]
            T1 = np.transpose(Ri.transpose()/Rnorm**3)
            T2[:,:,:] = np.ones((N-1,3,3))[:] * Ri.reshape((N-1,1,3)) * \
                        np.transpose(np.ones((N-1,3,3))*Ri.reshape((N-1,1,3)), (0,2,1))/ \
                        Rnorm.reshape((N-1,1,1))**5
            phi[i] = np.sum(q_temp[:]*T0[:]) + np.sum(T1[:]*d_temp[:]) + 0.5*np.sum(np.sum(T2[:]*Q_temp[:],axis=1))

        return phi
                
    def calculate_coulomb_dphi_multipole(self, flag_polar_group=True):
        """
        Calculates the first derivative of the potential due to the permanent multipoles

        flag_polar_group: (bool) consider polar groups in calculation
        """


        xq = self.x_q
        q = self.q
        d = self.d
        Q = self.Q
        alphaxx = self.alpha[:,0,0]
        thole = self.thole
        polar_group = self.polar_group

        dphi = self._calculate_coulomb_dphi_multipole(xq, q, d, Q, alphaxx, thole, polar_group, flag_polar_group)

        return dphi


    @staticmethod
    @jit(
        nopython=True, parallel=False, error_model="numpy", fastmath=True
    )
    def _calculate_coulomb_dphi_multipole(xq, q, d, Q, alphaxx, thole, polar_group, flag_polar_group):
        """
        Calculates the first derivative of the potential due to the permanent multipoles
        with numba jit

        flag_polar_group: (bool) consider polar groups in calculation
        """

        N = len(xq)
        T1 = np.zeros((3))
        T2 = np.zeros((3,3))
        eps = 1e-15
        
        scale3 = 1.0
        scale5 = 1.0
        scale7 = 1.0
        
        dphi = np.zeros((N,3))
        
        for i in range(N):
            
            aux = np.zeros((3))
            
            Ri = xq[i] - xq
            Rnorm = np.sqrt(np.sum((Ri*Ri), axis = 1) + eps*eps)
            
            for j in np.where(Rnorm>1e-12)[0]:
                
                R3 = Rnorm[j]**3
                R5 = Rnorm[j]**5
                R7 = Rnorm[j]**7
                
                if flag_polar_group==False:
                    
                    not_same_polar_group = True
                    
                else:
                    
                    gamma = min(thole[i], thole[j])
                    damp = (alphaxx[i]*alphaxx[j])**0.16666667
                    damp += 1e-12
                    damp = -1*gamma * (R3/(damp*damp*damp))
                    expdamp = np.exp(damp)
                    
                    scale3 = 1 - expdamp
                    scale5 = 1 - expdamp*(1-damp)
                    scale7 = 1 - expdamp*(1-damp+0.6*damp*damp)
                    
                    if polar_group[i]!=polar_group[j]:
                        
                        not_same_polar_group = True
                        
                    else:
                        
                        not_same_polar_group = False
                        
                if not_same_polar_group==True:
                    
                    for k in range(3):
                        
                        T0 = -Ri[j,k]/R3 * scale3
                        
                        for l in range(3):
                            
                            dkl = (k==l)*1.0
                            
                            T1[l] = dkl/R3 * scale3 - 3*Ri[j,k]*Ri[j,l]/R5 * scale5
                            
                            for m in range(3):
                                
                                dkm = (k==m)*1.0
                                T2[l][m] = (dkm*Ri[j,l]+dkl*Ri[j,m])/R5 * scale5 - 5*Ri[j,l]*Ri[j,m]*Ri[j,k]/R7 * scale7
             
                        
                        aux[k] += T0*q[j] + np.sum(T1*d[j]) + 0.5*np.sum(np.sum(T2[:,:]*Q[j,:,:], axis = 1), axis = 0)
                    
            dphi[i,:] += aux[:]
            
        return dphi

    def calculate_coulomb_ddphi_multipole(self):
        
        """
        Calculates the second derivative of the electrostatic potential of the permantent multipoles
        """
        xq = self.x_q
        q = self.q
        d = self.d
        Q = self.Q

        ddphi = self._calculate_coulomb_ddphi_multipole(xq, q, d, Q)

        return ddphi

    @staticmethod
    @jit(
        nopython=True, parallel=False, error_model="numpy", fastmath=True
    )
    def _calculate_coulomb_ddphi_multipole(xq, q, d, Q):
        
        """
        Calculates the second derivative of the electrostatic potential of the permantent multipoles
        with numba jit
        """
        T1 = np.zeros((3))
        T2 = np.zeros((3,3))
        
        eps = 1e-15
        
        N = len(xq)
        
        ddphi = np.zeros((N,3,3))
        
        for i in range(N):
            
            aux = np.zeros((3,3))
            
            Ri = xq[i] - xq
            Rnorm = np.sqrt(np.sum((Ri*Ri), axis = 1) + eps*eps)
            
            for j in np.where(Rnorm>1e-12)[0]:
                
                R3 = Rnorm[j]**3
                R5 = Rnorm[j]**5
                R7 = Rnorm[j]**7
                R9 = R3**3
                
                for k in range(3):
                    
                    for l in range(3):
                        
                        dkl = (k==l)*1.0
                        T0 = -dkl/R3 + 3*Ri[j,k]*Ri[j,l]/R5
                        
                        for m in range(3):
                            
                            dkm = (k==m)*1.0
                            dlm = (l==m)*1.0
                            
                            T1[m] = -3*(dkm*Ri[j,l]+dkl*Ri[j,m]+dlm*Ri[j,k])/R5 + 15*Ri[j,l]*Ri[j,m]*Ri[j,k]/R7
                            
                            for n in range(3):
                                
                                dkn = (k==n)*1.0
                                dln = (l==n)*1.0
                                
                                T2[m][n] = 35*Ri[j,k]*Ri[j,l]*Ri[j,m]*Ri[j,n]/R9 - 5*(Ri[j,m]*Ri[j,n]*dkl \
                                                                              + Ri[j,l]*Ri[j,n]*dkm \
                                                                              + Ri[j,m]*Ri[j,l]*dkn \
                                                                              + Ri[j,k]*Ri[j,n]*dlm \
                                                                              + Ri[j,m]*Ri[j,k]*dln)/R7 + (dkm*dln + dlm*dkn)/R5
                                
                        aux[k][l] += T0*q[j] + np.sum(T1[:]*d[j,:]) +  0.5*np.sum(np.sum(T2[:,:]*Q[j,:,:], axis = 1), axis = 0)
                        
            ddphi[i,:,:] += aux[:,:]
            
        return ddphi

    def calculate_coulomb_phi_multipole_Thole(self, state):
        """
        Calculates the potential due to the induced dipoles according to Thole

        state: (string) dissolved or vacuum, to choose which induced dipole to use
        """
        
        xq = self.x_q
        if state == "dissolved":
            induced_dipole = self.results["induced_dipole"]
        else:
            induced_dipole = self.results["induced_dipole_vacuum"]
        
        thole = self.thole
        alphaxx = self.alpha[:,0,0]
        connections_12 = self.connections_12
        pointer_connections_12 = self.pointer_connections_12
        connections_13 = self.connections_13
        pointer_connections_13 = self.pointer_connections_13
        p12scale = self.p12scale
        p13scale = self.p13scale

        phi = self._calculate_coulomb_phi_multipole_Thole(xq, induced_dipole, thole, alphaxx, connections_12, pointer_connections_12, connections_13, pointer_connections_13, p12scale, p13scale)

        return phi

    @staticmethod
    @jit(
        nopython=True, parallel=False, error_model="numpy", fastmath=True
    )
    def _calculate_coulomb_phi_multipole_Thole(xq, induced_dipole, thole, alphaxx, connections_12, pointer_connections_12, connections_13, pointer_connections_13, p12scale, p13scale):
        """
        Calculates the potential due to the induced dipoles according to Thole
        with numba jit

        """
        
        eps = 1e-15
        T1 = np.zeros((3))
        
        N = len(xq)
        
        phi = np.zeros((N))
        
        for i in range(N):
            
            aux = 0.
            start_12 = pointer_connections_12[i]
            stop_12 = pointer_connections_12[i+1]
            start_13 = pointer_connections_13[i]
            stop_13 = pointer_connections_13[i+1]
            
            Ri = xq[i] - xq
            
            r = 1./np.sqrt(np.sum((Ri*Ri), axis = 1) + eps*eps)
            
            for j in np.where(r<1e12)[0]:
                
                pscale = 1.0
                
                for ii in range(start_12, stop_12):
                    
                    if connections_12[ii]==j:
                        
                        pscale = p12scale
                        
                for ii in range(start_13, stop_13):
                    
                    if connections_13[ii]==j:
                        
                        pscale = p13scale
                        
                r3 = r[j]**3
                
                gamma = min(thole[i], thole[j])
                damp = (alphaxx[i]*alphaxx[j])**0.16666667
                damp += 1e-12
                damp = -gamma * (1/(r3*damp**3))
                expdamp = np.exp(damp)
                
                scale3 = 1 - expdamp
                
                for k in range(3):
                    
                    T1[k] = Ri[j,k]*r3*scale3*pscale
                    
                aux += np.sum(T1[:]*induced_dipole[j,:])
                
            phi[i] += aux
        
        return phi

    def calculate_coulomb_dphi_multipole_Thole(self, state):

        """
        Calculates the derivative of the potential due to the induced dipoles according to Thole

        state: (string) dissolved or vacuum, to choose which induced dipole to use
        """
        
        xq = self.x_q
        if state == "dissolved":
            induced_dipole = self.results["induced_dipole"]
        elif state == "vacuum":
            induced_dipole = self.results["induced_dipole_vacuum"]
        else:
            print("Cannot understand state")
        
        thole = self.thole
        connections_12 = self.connections_12
        pointer_connections_12 = self.pointer_connections_12
        connections_13 = self.connections_13
        pointer_connections_13 = self.pointer_connections_13
        p12scale = self.p12scale
        p13scale = self.p13scale
        alphaxx = self.alpha[:,0,0]
        
        dphi = self._calculate_coulomb_dphi_multipole_Thole(xq, induced_dipole, thole, alphaxx, connections_12, pointer_connections_12, connections_13, pointer_connections_13, p12scale, p13scale)

        return dphi

    @staticmethod
    @jit(
        nopython=True, parallel=False, error_model="numpy", fastmath=True
    )
    def _calculate_coulomb_dphi_multipole_Thole(xq, induced_dipole, thole, alphaxx, connections_12, pointer_connections_12, connections_13, pointer_connections_13, p12scale, p13scale):

        """
        Calculates the derivative of the potential due to the induced dipoles according to Thole
        with numba jit
        """
        
        eps = 1e-15
        T1 = np.zeros((3))
        
        N = len(xq)
        
        dphi = np.zeros((N,3))
        
        for i in range(N):
            
            aux = np.zeros((3))
            
            start_12 = pointer_connections_12[i]
            stop_12 = pointer_connections_12[i+1]
            start_13 = pointer_connections_13[i]
            stop_13 = pointer_connections_13[i+1]
            
            Ri = xq[i] - xq
            r = 1./np.sqrt(np.sum((Ri*Ri), axis = 1) + eps*eps)
            
            for j in np.where(r<1e12)[0]:
                
                pscale = 1.0
                
                for ii in range(start_12, stop_12):
                    
                    if connections_12[ii]==j:
                        
                        pscale = p12scale
                        
                for ii in range(start_13, stop_13):
                    
                    if connections_13[ii]==j:
                        
                        pscale = p13scale
                        
                r3 = r[j]**3
                r5 = r[j]**5
                
                gamma = min(thole[i], thole[j])
                damp = (alphaxx[i]*alphaxx[j])**0.16666667
                damp += 1e-12
                damp = -gamma * (1/(r3*damp**3))
                expdamp = np.exp(damp)
                
                scale3 = 1 - expdamp
                scale5 = 1 - expdamp*(1 - damp)
                
                for k in range(3):
                    
                    for l in range(3):
                        
                        dkl = (k==l)*1.0
                        T1[l] = scale3*dkl*r3*pscale - scale5*3*Ri[j,k]*Ri[j,l]*r5*pscale
                        
                    aux[k] += np.sum(T1[:] * induced_dipole[j,:])
                    
            dphi[i,:] += aux[:]

        return dphi

    def calculate_coulomb_ddphi_multipole_Thole(self, state):
        """
        Calculates the second derivative of the potential due to the induced dipoles according to Thole

        state: (string) dissolved or vacuum, to choose which induced dipole to use
        """
        
        xq = self.x_q
        
        if state == "dissolved":
            induced_dipole = self.results["induced_dipole"]
        else:
            induced_dipole = self.results["induced_dipole_vacuum"]
        
        thole = self.thole
        connections_12 = self.connections_12
        pointer_connections_12 = self.pointer_connections_12
        connections_13 = self.connections_13
        pointer_connections_13 = self.pointer_connections_13
        p12scale = self.p12scale
        p13scale = self.p13scale
        alphaxx = self.alpha[:,0,0]

        ddphi = self._calculate_coulomb_ddphi_multipole_Thole(xq, induced_dipole, thole, alphaxx, connections_12, pointer_connections_12, connections_13, pointer_connections_13, p12scale, p13scale)

        return ddphi

    @staticmethod
    @jit(
        nopython=True, parallel=False, error_model="numpy", fastmath=True
    )
    def _calculate_coulomb_ddphi_multipole_Thole(xq, induced_dipole, thole, alphaxx, connections_12, pointer_connections_12, connections_13, pointer_connections_13, p12scale, p13scale):

        """
        Calculates the second derivative of the potential due to the induced dipoles according to Thole
        with numba jit
        """

        eps = 1e-15
        T1 = np.zeros((3))
        
        N = len(xq)
        
        ddphi = np.zeros((N,3,3))
        
        for i in range(N):
            
            aux = np.zeros((3,3))
            
            start_12 = pointer_connections_12[i]
            stop_12 = pointer_connections_12[i+1]
            start_13 = pointer_connections_13[i]
            stop_13 = pointer_connections_13[i+1]
            
            Ri = xq[i] - xq
            r = 1./np.sqrt(np.sum((Ri*Ri), axis = 1) + eps*eps)
            
            for j in np.where(r<1e12)[0]:
                
                pscale = 1.0
                
                for ii in range(start_12, stop_12):
                    
                    if connections_12[ii]==j:
                        
                        pscale = p12scale
                        
                for ii in range(start_13, stop_13):
                    
                    if connections_13[ii]==j:
                        
                        pscale = p13scale
                        
                r3 = r[j]**3
                r5 = r[j]**5
                r7 = r[j]**7
                
                gamma = min(thole[i], thole[j])
                damp = (alphaxx[i]*alphaxx[j])**0.16666667
                damp += 1e-12
                damp = -gamma * (1/(r3*damp**3))
                expdamp = np.exp(damp)
                
                scale5 = 1 - expdamp*(1 - damp)
                scale7 = 1 - expdamp*(1 - damp + 0.6*damp**2)
                
                for k in range(3):
                    
                    for l in range(3):
                        
                        dkl = (k==l)*1.0
                        
                        for m in range(3):
                            
                            dkm = (k==m)*1.0
                            dlm = (l==m)*1.0
                            
                            T1[m] = -3*(dkm*Ri[j,l] + dkl*Ri[j,m] + dlm*Ri[j,k])*r5*scale5*pscale \
                            + 15*Ri[j,l]*Ri[j,m]*Ri[j,k]*r7*scale7*pscale
                            
                        aux[k][l] += np.sum(T1[:]*induced_dipole[j,:])
                        
            ddphi[i,:,:] += aux[:,:]
            
        return ddphi
            

def get_name_from_pdb(pdb_path):
    pdb_file = open(pdb_path)
    first_line = pdb_file.readline()
    first_line_split = re.split(r"\s{2,}", first_line)
    solute_name = first_line_split[3].lower()
    pdb_file.close()

    return solute_name
