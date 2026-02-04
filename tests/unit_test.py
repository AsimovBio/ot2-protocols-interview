import json
import unittest


from ot2protocols import app
from ot2protocols import utils


class OT2ProtocolsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app.generate_app().test_client()

    def tearDown(self):
        pass

    def test_index(self):
        response = self.app.get("/")
        self.assertEqual(response.status_code, 200)

    def test_health(self):
        response = self.app.get("/health")
        self.assertEqual(response.status_code, 200)

    def test_labware_view(self):
        response = self.app.get("/protocols/labware")
        self.assertEqual(response.status_code, 200)

    def test_labware_api(self):
        response = self.app.post("api/protocols/labware")
        self.assertEqual(response.status_code, 200)

    def test_calibrate_view(self):
        response = self.app.get("/protocols/calibrate")
        self.assertEqual(response.status_code, 200)

    def test_calibrate_form(self):
        response = self.app.post("/protocols/calibrate",
                                 data={"config_name": "highvol",
                                       "item_name": "96_PCR_flat"})
        self.assertEqual(response.status_code, 200)

    def test_elisa_view(self):
        response = self.app.get("/protocols/elisa")
        self.assertEqual(response.status_code, 200)

    def test_elisa_api(self):
        """Run some very basic tests of the ELISA API endpoint"""
        response = self.app.post("api/protocols/elisa",
                                 data=json.dumps({
                                    "num_samples": 5,
                                    "version": 1
                                 }),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 200)

        response = self.app.post("api/protocols/elisa",
                                 data=json.dumps({
                                    "num_samples": 5
                                 }),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 200)

        response = self.app.post("api/protocols/elisa",
                                 data=json.dumps({
                                    "num_samples": "asdfa"
                                 }),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 500)

    def run_protocol_util(self, protocol_string):
        """
        Install custom labware in test environment and simulate the
        provided protocol string using the opentrons package.
        """
        labware_response = self.app.post("api/protocols/labware",
                                         data="{}",
                                         content_type="application/json")
        labware_json = json.loads(labware_response.get_data(as_text=True))
        labware_string = labware_json["protocol_string"]
        exec((labware_string + "\n\n" + protocol_string),
             globals(), globals())

    def protocol_test_util(self, route, data):
        """
        Test an actual protocol returned by an API endpoint.
        This uses the official opentrons package (included as a dependency in
        the tox test environment) to simulate the protocol.
        """
        response = self.app.post(route,
                                 data=json.dumps(data),
                                 content_type="application/json")
        response_json = json.loads(response.get_data(as_text=True))
        self.run_protocol_util(response_json["protocol_string"])

    def test_elisa_protocol(self):
        """
        Test the actual protocol returned by the ELISA API endpoint.
        """
        self.protocol_test_util("api/protocols/elisa", {"num_samples": 5})

    def test_labware_protocol(self):
        """
        Simulate the labware creation protocol using the opentrons package.
        """
        self.protocol_test_util("api/protocols/labware", {})

    def calibrate_test_util(self, config_name, item_name):
        """
        Test an actual protocol for calibrating a piece of labware
        using the opentrons package.
        """
        data = {"config_name": config_name, "item_name": item_name}
        response = self.app.post("protocols/calibrate", data=data)
        protocol_string = response.get_data(as_text=True)
        self.run_protocol_util(protocol_string)

    def test_calibrations(self):
        calibrations = {
            "highvol": [
                "96_PCR_flat",
                "tube_rack_48_cold_block",
                "384_plate",
                "tiprack_200ul",
                "96_flat",
                "pipettes"
            ],
            "lowvol": [
                "96_PCR_flat",
                "tube_rack_48_cold_block",
                "384_plate",
                "tiprack_200ul",
                "tiprack_10ul",
                "96_flat",
                "pipettes"
            ]
        }
        for config, item in calibrations.items():
            self.calibrate_test_util(config, item)


if __name__ == "__main__":
    unittest.main()
